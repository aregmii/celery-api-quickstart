"""
Redis service for atomic credit operations and caching.

Credit flow:
- reserve_credits(): task submitted → hold credits
- commit_credits(): task succeeded → reserved → spent
- rollback_credits(): task failed → reserved → available

All credit ops use Lua scripts for atomicity.
"""
import uuid
import json
from typing import Optional, Dict, Any
import redis
from logger import get_logger

logger = get_logger(__name__)


# Lua: reserve credits AND create task atomically (single network call)
# Combines credit reservation + task cache creation for speed optimization
# if user not in Redis → USER_NOT_FOUND
# if available < credits → INSUFFICIENT_CREDITS
# else → reserve credits, create reservation, create task cache
RESERVE_AND_CREATE_TASK_SCRIPT = """
local user_key = KEYS[1]
local reservation_key = KEYS[2]
local task_key = KEYS[3]
local credits = tonumber(ARGV[1])
local reservation_id = ARGV[2]
local task_id = ARGV[3]
local owner_api_key = ARGV[4]
local a = ARGV[5]
local b = ARGV[6]

if redis.call('EXISTS', user_key) == 0 then
    return cjson.encode({err = 'USER_NOT_FOUND'})
end

local total = tonumber(redis.call('HGET', user_key, 'total_credits') or 0)
local reserved = tonumber(redis.call('HGET', user_key, 'reserved_credits') or 0)
local spent = tonumber(redis.call('HGET', user_key, 'spent_credits') or 0)
local available = total - reserved - spent

if available < credits then
    return cjson.encode({err = 'INSUFFICIENT_CREDITS', available = available})
end

-- All operations below are atomic (single Redis call)
-- 1. Reserve credits
redis.call('HINCRBY', user_key, 'reserved_credits', credits)

-- 2. Create reservation record
redis.call('HSET', reservation_key, 'user_key', user_key, 'credits', credits, 'task_id', task_id)

-- 3. Create task cache (for fast polling)
redis.call('HSET', task_key,
    'owner_api_key', owner_api_key,
    'a', a,
    'b', b,
    'credits_required', credits,
    'status', 'pending',
    'result', '',
    'error_message', '',
    'reservation_id', reservation_id
)

return cjson.encode({ok = true, reservation_id = reservation_id})
"""

# Lua: rollback credits (task failed or submission rollback)
# reserved -= credits, delete reservation
ROLLBACK_CREDITS_SCRIPT = """
local reservation_key = KEYS[1]
local user_key = redis.call('HGET', reservation_key, 'user_key')
local credits = tonumber(redis.call('HGET', reservation_key, 'credits') or 0)

if not user_key or credits == 0 then
    return cjson.encode({err = 'RESERVATION_NOT_FOUND'})
end

redis.call('HINCRBY', user_key, 'reserved_credits', -credits)
redis.call('DEL', reservation_key)
return cjson.encode({ok = true, released = credits})
"""

# Lua: commit credits (task succeeded)
# reserved -= credits, spent += credits, delete reservation
COMMIT_CREDITS_SCRIPT = """
local reservation_key = KEYS[1]
local user_key = redis.call('HGET', reservation_key, 'user_key')
local credits = tonumber(redis.call('HGET', reservation_key, 'credits') or 0)

if not user_key or credits == 0 then
    return cjson.encode({err = 'RESERVATION_NOT_FOUND'})
end

redis.call('HINCRBY', user_key, 'reserved_credits', -credits)
redis.call('HINCRBY', user_key, 'spent_credits', credits)
redis.call('DEL', reservation_key)
return cjson.encode({ok = true, committed = credits})
"""


class RedisService:
    """Atomic Redis operations for credits and caching."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self._reserve_and_create_task_script = self.redis.register_script(RESERVE_AND_CREATE_TASK_SCRIPT)
        self._rollback_script = self.redis.register_script(ROLLBACK_CREDITS_SCRIPT)
        self._commit_script = self.redis.register_script(COMMIT_CREDITS_SCRIPT)

    # --- Credit Operations ---

    def reserve_credits_and_create_task(
        self,
        api_key: str,
        credits: int,
        task_id: str,
        a: int,
        b: int
    ) -> Optional[str]:
        """
        Reserve credits AND create task cache in ONE atomic Lua script call.

        This combines what was previously 2 Redis calls into 1 for speed optimization.
        Creates: reservation record + task cache (for fast polling)

        Returns reservation_id if success else raises.
        Raises InvalidApiKeyError if user not in Redis.
        Raises InsufficientCreditsError if available < credits.
        """
        from exceptions import InvalidApiKeyError, InsufficientCreditsError

        reservation_id = str(uuid.uuid4())
        user_key = f"user:{api_key}"
        reservation_key = f"reservation:{reservation_id}"
        task_key = f"task:{task_id}"

        try:
            result = self._reserve_and_create_task_script(
                keys=[user_key, reservation_key, task_key],
                args=[credits, reservation_id, task_id, api_key, a, b]
            )
            data = json.loads(result)

            if 'err' in data:
                if data['err'] == 'USER_NOT_FOUND':
                    raise InvalidApiKeyError()
                elif data['err'] == 'INSUFFICIENT_CREDITS':
                    raise InsufficientCreditsError()
                return None

            logger.info(f"reserve+create: {credits} credits, task={task_id[:8]}")
            return reservation_id

        except (redis.RedisError, json.JSONDecodeError) as e:
            logger.error(f"reserve+create failed: {e}")
            return None

    def rollback_credits(self, reservation_id: str) -> bool:
        """
        Rollback reserved credits. Called on task failure or submission rollback.

        reserved -= credits, delete reservation.
        """
        reservation_key = f"reservation:{reservation_id}"

        try:
            result = self._rollback_script(keys=[reservation_key], args=[])
            data = json.loads(result)

            if 'err' in data:
                logger.warning(f"rollback failed: {data['err']}")
                return False

            logger.info(f"rollback: {data.get('released', 0)} credits")
            return True

        except (redis.RedisError, json.JSONDecodeError) as e:
            logger.error(f"rollback failed: {e}")
            return False

    def commit_credits(self, reservation_id: str) -> bool:
        """
        Commit reserved credits to spent. Called on task success.

        reserved -= credits, spent += credits, delete reservation.
        """
        reservation_key = f"reservation:{reservation_id}"

        try:
            result = self._commit_script(keys=[reservation_key], args=[])
            data = json.loads(result)

            if 'err' in data:
                logger.warning(f"commit failed: {data['err']}")
                return False

            logger.info(f"commit: {data.get('committed', 0)} credits")
            return True

        except (redis.RedisError, json.JSONDecodeError) as e:
            logger.error(f"commit failed: {e}")
            return False

    # --- User Operations ---

    def get_user(self, api_key: str) -> Optional[Dict[str, Any]]:
        """Get user from Redis. Returns None if not found."""
        user_key = f"user:{api_key}"
        try:
            data = self.redis.hgetall(user_key)
            if not data:
                return None
            return {
                'name': data.get('name', ''),
                'api_key': api_key,
                'total_credits': int(data.get('total_credits', 0)),
                'reserved_credits': int(data.get('reserved_credits', 0)),
                'spent_credits': int(data.get('spent_credits', 0)),
            }
        except redis.RedisError:
            return None

    def set_user(self, api_key: str, name: str, total_credits: int,
                 spent_credits: int, reserved_credits: int = 0) -> bool:
        """Set user in Redis."""
        user_key = f"user:{api_key}"
        try:
            self.redis.hset(user_key, mapping={
                'name': name,
                'total_credits': total_credits,
                'reserved_credits': reserved_credits,
                'spent_credits': spent_credits,
            })
            return True
        except redis.RedisError:
            return False

    def update_user_total_credits(self, api_key: str, total_credits: int) -> bool:
        """Update total_credits field only."""
        user_key = f"user:{api_key}"
        try:
            self.redis.hset(user_key, 'total_credits', total_credits)
            return True
        except redis.RedisError:
            return False

    def user_exists(self, api_key: str) -> bool:
        """Check if user in Redis."""
        try:
            return self.redis.exists(f"user:{api_key}") > 0
        except redis.RedisError:
            return False

    # --- Task Operations ---

    def create_task(self, task_id: str, owner_api_key: str, a: int, b: int,
                    credits_required: int, reservation_id: str) -> bool:
        """Create task in Redis."""
        try:
            self.redis.hset(f"task:{task_id}", mapping={
                'owner_api_key': owner_api_key,
                'a': a,
                'b': b,
                'credits_required': credits_required,
                'status': 'pending',
                'result': '',
                'error_message': '',
                'reservation_id': reservation_id,
            })
            return True
        except redis.RedisError:
            return False

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task from Redis. Returns None if not found."""
        try:
            data = self.redis.hgetall(f"task:{task_id}")
            if not data:
                return None
            return {
                'id': task_id,
                'owner_api_key': data.get('owner_api_key', ''),
                'a': int(data.get('a', 0)),
                'b': int(data.get('b', 0)),
                'credits_required': int(data.get('credits_required', 1)),
                'status': data.get('status', 'pending'),
                'result': int(data['result']) if data.get('result') else None,
                'error_message': data.get('error_message') or None,
                'reservation_id': data.get('reservation_id', ''),
            }
        except redis.RedisError:
            return None

    def update_task_status(self, task_id: str, status: str,
                           result: Optional[int] = None,
                           error_message: Optional[str] = None) -> bool:
        """Update task status in Redis."""
        try:
            mapping = {'status': status}
            if result is not None:
                mapping['result'] = result
            if error_message is not None:
                mapping['error_message'] = error_message
            self.redis.hset(f"task:{task_id}", mapping=mapping)
            return True
        except redis.RedisError:
            return False

    def delete_task(self, task_id: str) -> bool:
        """Delete task from Redis."""
        try:
            self.redis.delete(f"task:{task_id}")
            return True
        except redis.RedisError:
            return False

    def task_exists(self, task_id: str) -> bool:
        """Check if task in Redis."""
        try:
            return self.redis.exists(f"task:{task_id}") > 0
        except redis.RedisError:
            return False

    # --- Reservation Operations (for recovery sync) ---

    def increment_reserved_credits(self, api_key: str, amount: int) -> bool:
        """Increment reserved_credits. Used by startup sync."""
        try:
            self.redis.hincrby(f"user:{api_key}", 'reserved_credits', amount)
            return True
        except redis.RedisError:
            return False

    def create_reservation(self, reservation_id: str, api_key: str,
                           credits: int, task_id: str) -> bool:
        """Create reservation. Used by startup sync."""
        try:
            self.redis.hset(f"reservation:{reservation_id}", mapping={
                'user_key': f"user:{api_key}",
                'credits': credits,
                'task_id': task_id,
            })
            return True
        except redis.RedisError:
            return False
