-- Multi-key BYOK: second Anthropic API key for key-pool throughput boost.

ALTER TABLE users ADD COLUMN IF NOT EXISTS anthropic_api_key_2 TEXT;
