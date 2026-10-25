"""
Encryption utility for securing sensitive data like API keys
"""

import os
import base64
import warnings
from cryptography.fernet import Fernet


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

    @staticmethod
    def _coerce_fernet_key(raw: str) -> bytes:
        """Turn whatever ENCRYPTION_KEY contains into bytes Fernet accepts.

        A Fernet key IS urlsafe-base64 text, so the normal case is to pass it
        straight through. Shell variables pick up stray whitespace and quotes
        easily, and a value that has been base64-encoded a second time by a
        secret store is a realistic mistake, so both are tolerated rather
        than failing with Fernet's opaque message.
        """
        candidate = raw.strip().strip("'\"").encode()

        try:
            Fernet(candidate)
            return candidate
        except Exception:
            pass

        # Maybe it is the 32 raw key bytes, themselves base64-encoded again.
        try:
            decoded = base64.urlsafe_b64decode(candidate)
            if len(decoded) == 32:
                reencoded = base64.urlsafe_b64encode(decoded)
                Fernet(reencoded)
                return reencoded
        except Exception:
            pass

        raise ValueError(
            "ENCRYPTION_KEY is not a valid Fernet key. It must be 44 "
            "url-safe base64 characters, exactly as found in encryption.key. "
            'Generate one with: python -c "from cryptography.fernet import '
            'Fernet; print(Fernet.generate_key().decode())"  — but note that a '
            "NEW key cannot decrypt credentials stored under the old one."
        )

    def _get_or_create_key(self) -> bytes:
        """Get existing encryption key or create new one"""
        # Use absolute path for key file to ensure consistency
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        default_key_path = os.path.join(base_dir, "encryption.key")
        key_path = os.environ.get("ENCRYPTION_KEY_FILE_PATH", default_key_path)

        # Check for key in environment variable first.
        #
        # This path used to be unusable. It did:
        #     return base64.urlsafe_b64decode(env_key.encode())
        # which turns a real Fernet key (44 urlsafe-base64 characters) into 32
        # RAW bytes — and Fernet() then rejects that with "Fernet key must be
        # 32 url-safe base64-encoded bytes." So setting ENCRYPTION_KEY crashed
        # the app at startup, every time. That matters because this is the
        # documented production route: the error raised further down says
        # "ENCRYPTION_KEY environment variable must be set in production",
        # i.e. anyone following that instruction hit a hard failure. Only the
        # key-FILE path ever worked, which is why it went unnoticed.
        env_key = os.environ.get("ENCRYPTION_KEY")
        if env_key:
            return self._coerce_fernet_key(env_key)

        # Check for key file
        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                return f.read().strip()

        # SECURITY: mirror config.py's SECRET_KEY requirement — refuse to
        # silently generate (and write unencrypted to disk) a brand-new
        # encryption key in production. A key generated after deployment
        # can't decrypt any credential that was already encrypted with a
        # previous key, and a key sitting in a plaintext file is only as
        # secure as the filesystem it's on.
        env = os.environ.get("FLASK_ENV", os.environ.get("ENV", "development"))
        if env == "production":
            raise ValueError(
                "ENCRYPTION_KEY environment variable must be set in production "
                "(no encryption.key file found either)."
            )

        # Generate new key
        key = Fernet.generate_key()

        # Save key file
        with open(key_path, "wb") as f:
            f.write(key)

        # Add to .gitignore if not already there
        self._add_to_gitignore(key_path)

        warnings.warn(
            f"ENCRYPTION_KEY not set — generated a new key at {key_path} for "
            "development. Do NOT use this in production. Set ENCRYPTION_KEY "
            "env var before deployment.",
            UserWarning,
        )

        return key

    def _add_to_gitignore(self, filepath: str):
        """Make sure a generated key file is ignored by git.

        Writes a path RELATIVE to the repository, and only for a key that
        actually lives inside it. Previously this appended the absolute
        path it was handed, which meant lines like

            C:\\Users\\someone\\AppData\\Local\\Temp\\...\\encryption.key

        were added to the committed .gitignore — machine-specific, useless
        to anyone else, and they really did get committed. A key written
        outside the repository (ENCRYPTION_KEY_FILE_PATH pointing at /tmp,
        which is what the test suite does) needs no gitignore entry at all,
        so nothing is written in that case.
        """
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        gitignore_path = os.path.join(base_dir, ".gitignore")

        try:
            rel = os.path.relpath(os.path.abspath(filepath), base_dir)
        except ValueError:
            # Different drive on Windows — definitively outside the repo.
            return
        if rel.startswith(os.pardir) or os.path.isabs(rel):
            return
        entry = rel.replace(os.sep, "/")

        if not os.path.exists(gitignore_path):
            with open(gitignore_path, "w", encoding="utf-8") as f:
                f.write(f"{entry}\n")
            return

        with open(gitignore_path, "r", encoding="utf-8") as f:
            content = f.read()

        if entry not in content.split():
            with open(gitignore_path, "a", encoding="utf-8") as f:
                f.write(f"\n{entry}\n")

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

        Raises:
            Exception: if any field fails to decrypt. A rotated/wrong key
            must not silently leave raw ciphertext in place of the real
            value — a caller using the returned dict (e.g. as an
            Authorization header) would send garbage to a live API without
            any indication it wasn't the real credential.
        """
        decrypted_dict = data_dict.copy()

        for field in fields_to_decrypt:
            if field in decrypted_dict and decrypted_dict[field]:
                decrypted_dict[field] = self.decrypt(str(decrypted_dict[field]))

        return decrypted_dict


# Singleton instance
_encryption_service = None


def get_encryption_service() -> EncryptionService:
    """Get the singleton encryption service instance"""
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    return _encryption_service
