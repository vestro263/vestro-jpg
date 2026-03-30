# vestro_backend/app/services/credential_store.py
from cryptography.fernet import Fernet
import os

_fernet = Fernet(os.environ["ENCRYPTION_KEY"].encode())

def encrypt(val: str) -> str:
    return _fernet.encrypt(val.encode()).decode()

def decrypt(val: str) -> str:
    return _fernet.decrypt(val.encode()).decode()