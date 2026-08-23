"""
Encryption utility for securing sensitive data like API keys
"""
import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from typing import Optional


class EncryptionService:
    """Service for encrypting and decrypting sensitive data"""
    
    _instance = None
    _cipher = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(EncryptionService, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize encryption service"""
        if self._cipher is not None:
            return
        
        key = self._get_or_create_key()
        self._cipher = Fernet(key)
    
    def _get_or_create_key(self) -> bytes:
        """Get existing encryption key or create new one"""
        # Use absolute path for key file to ensure consistency
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        default_key_path = os.path.join(base_dir, 'encryption.key')
        key_path = os.environ.get('ENCRYPTION_KEY_FILE_PATH', default_key_path)
        
        # Check for key in environment variable first
        env_key = os.environ.get('ENCRYPTION_KEY')
        if env_key:
            return base64.urlsafe_b64decode(env_key.encode())
        
        # Check for key file
        if os.path.exists(key_path):
            with open(key_path, 'rb') as f:
                return f.read()
        
        # Generate new key
        key = Fernet.generate_key()
        
        # Save key file
        with open(key_path, 'wb') as f:
            f.write(key)
        
        # Add to .gitignore if not already there
        self._add_to_gitignore(key_path)
        
        print(f"Generated new encryption key at {key_path}")
        print("IMPORTANT: Keep this file secure and back it up!")
        print("Set ENCRYPTION_KEY environment variable for production")
        
        return key
    
    def _add_to_gitignore(self, filepath: str):
        """Add file to .gitignore if not present"""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        gitignore_path = os.path.join(base_dir, '.gitignore')
        
        if not os.path.exists(gitignore_path):
            with open(gitignore_path, 'w') as f:
                f.write(f"{filepath}\n")
            return
        
        with open(gitignore_path, 'r') as f:
            content = f.read()
        
        if filepath not in content:
            with open(gitignore_path, 'a') as f:
                f.write(f"\n{filepath}\n")
    
    def encrypt(self, data: str) -> str:
        """
        Encrypt string data
        
        Args:
            data: String to encrypt
            
        Returns:
            Encrypted string (base64 encoded)
        """
        if not data:
            return ""
        
        try:
            encrypted = self._cipher.encrypt(data.encode())
            return encrypted.decode()
        except Exception as e:
            print(f"Encryption error: {str(e)}")
            raise
    
    def decrypt(self, encrypted_data: str) -> str:
        """
        Decrypt encrypted string data
        
        Args:
            encrypted_data: Encrypted string to decrypt
            
        Returns:
            Decrypted string
        """
        if not encrypted_data:
            return ""
        
        try:
            decrypted = self._cipher.decrypt(encrypted_data.encode())
            return decrypted.decode()
        except Exception as e:
            print(f"Decryption error: {str(e)}")
            raise
    
    def encrypt_dict(self, data_dict: dict, fields_to_encrypt: list) -> dict:
        """
        Encrypt specific fields in a dictionary
        
        Args:
            data_dict: Dictionary containing data
            fields_to_encrypt: List of field names to encrypt
            
        Returns:
            Dictionary with specified fields encrypted
        """
        encrypted_dict = data_dict.copy()
        
        for field in fields_to_encrypt:
            if field in encrypted_dict and encrypted_dict[field]:
                encrypted_dict[field] = self.encrypt(str(encrypted_dict[field]))
        
        return encrypted_dict
    
    def decrypt_dict(self, data_dict: dict, fields_to_decrypt: list) -> dict:
        """
        Decrypt specific fields in a dictionary
        
        Args:
            data_dict: Dictionary containing encrypted data
            fields_to_decrypt: List of field names to decrypt
            
        Returns:
            Dictionary with specified fields decrypted
        """
        decrypted_dict = data_dict.copy()
        
        for field in fields_to_decrypt:
            if field in decrypted_dict and decrypted_dict[field]:
                try:
                    decrypted_dict[field] = self.decrypt(str(decrypted_dict[field]))
                except Exception as e:
                    print(f"Failed to decrypt field {field}: {str(e)}")
                    # Keep original value if decryption fails
                    pass
        
        return decrypted_dict


# Singleton instance
_encryption_service = None

def get_encryption_service() -> EncryptionService:
    """Get the singleton encryption service instance"""
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    return _encryption_service
