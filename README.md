# Toy API

Here is some starter code for an asynchronous toy API. To get up and running:

```
docker compose up --build
```

After it built, the following will be available:

- a redis instance
- a postgres database with a prepopulated `users` table
    - The `users` table contains `name, api_key, credits` columns, see `sql/create_tables.sql`.
- a fastapi instance running at port 8000 with the following endpoints:
    - `http://localhost:8000/hit` an endpoint that hits the redis cache
    - `http://localhost:8000/users` an endpoint that lists the users in postgresql
- a worker container defined under `worker/` which is currently not connected to the API.

Your goal is to provide an asynchronous endpoint for the worker together with authentication and credit deduction functionality.

## Async Task Endpoint

There should be a `/task` endpoint that accepts 2 integer parameters and returns a task id. There should then also be a `/poll` endpoint that accepts a task id and checks whether such a task exists and if so returns the result.
You can either use an existing task queue such as celery (you can also add other services such as rabbitmq to the compose file for this) or roll your own.
Provide an example script that submits a tasks and polls for it until the result is received.

## Authentication and Billing

The task endpoint should only be available for authenticated users. Authentication should happen via a bearer token which is checked against the prepopulated `api_key` in the database.

Users should only be able to call the task endpoint if they have sufficient credits. You can choose an arbitrary cost for the call.

The admin user should have an endpoint to update the amount of credits a given user has.

Assuming the database calls are too expensive, how can we reduce the number of calls?
