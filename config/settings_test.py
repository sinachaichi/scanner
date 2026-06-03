"""
Test settings: sets required env vars before importing the production settings,
so tests run without a real .env file.
"""
import os

from cryptography.fernet import Fernet

os.environ.setdefault('SECRET_KEY', 'test-only-key-not-for-production-use')
os.environ.setdefault('FIELD_ENCRYPTION_KEY', Fernet.generate_key().decode())

from config.settings import *  # noqa: E402, F401, F403, wildcard OK for settings override
