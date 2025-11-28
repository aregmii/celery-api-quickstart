-- Create the users table
CREATE TABLE users (
    name VARCHAR(255) NOT NULL,
    api_key CHAR(36) NOT NULL,
    credits INT NOT NULL,
    PRIMARY KEY (api_key)
);

-- Insert test data
INSERT INTO users (name, api_key, credits) VALUES
('admin',      '123e4567-e89b-12d3-a456-426614174000', 1000),
('test_user1', '550e8400-e29b-41d4-a716-446655440000',  500),
('test_user2', 'c56a4180-65aa-42ec-a945-5fd21dec0538',  250);

-- Create the tasks table
CREATE TABLE tasks (
    id UUID PRIMARY KEY,
    owner_api_key CHAR(36) NOT NULL REFERENCES users(api_key),
    a INT NOT NULL,
    b INT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    result INT,
    error_message TEXT,
    retry_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Index for faster lookups by owner
CREATE INDEX idx_tasks_owner ON tasks(owner_api_key);
CREATE INDEX idx_tasks_status ON tasks(status);
