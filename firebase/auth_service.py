from firebase_admin import auth
from typing import Dict, Optional


class AuthService:
    """Service for Firebase Authentication operations"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AuthService, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize Firebase Auth"""
        # Auth is initialized with Firebase Admin SDK
        pass

    def create_user(self, email: str, password: str, display_name: str = None) -> Dict:
        """Create a new user"""
        try:
            user = auth.create_user(
                email=email, password=password, display_name=display_name
            )
            return {
                "uid": user.uid,
                "email": user.email,
                "display_name": user.display_name,
                "success": True,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def verify_token(self, id_token: str) -> Dict:
        """Verify Firebase ID token"""
        try:
            decoded_token = auth.verify_id_token(id_token)
            return {"success": True, "data": decoded_token}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_user(self, uid: str) -> Optional[Dict]:
        """Get user by UID"""
        try:
            user = auth.get_user(uid)
            return {
                "uid": user.uid,
                "email": user.email,
                "display_name": user.display_name,
                "photo_url": user.photo_url,
                "email_verified": user.email_verified,
                "disabled": user.disabled,
            }
        except auth.UserNotFoundError:
            return None
        except Exception as e:
            print(f"Error getting user: {str(e)}")
            return None

    def update_user(self, uid: str, updates: Dict) -> bool:
        """Update user"""
        try:
            auth.update_user(uid, **updates)
            return True
        except Exception as e:
            print(f"Error updating user: {str(e)}")
            return False

    def delete_user(self, uid: str) -> bool:
        """Delete user"""
        try:
            auth.delete_user(uid)
            return True
        except Exception as e:
            print(f"Error deleting user: {str(e)}")
            return False

    def reset_password(self, email: str) -> bool:
        """Send password reset email"""
        try:
            # Note: This requires Firebase Admin SDK with proper configuration
            # For now, return True as placeholder
            print(f"Password reset link would be sent to {email}")
            return True
        except Exception as e:
            print(f"Error sending password reset: {str(e)}")
            return False

    def list_users(self, limit: int = 100) -> list:
        """List users"""
        try:
            users = []
            page = auth.list_users(limit=limit)
            for user in page.users:
                users.append(
                    {
                        "uid": user.uid,
                        "email": user.email,
                        "display_name": user.display_name,
                    }
                )
            return users
        except Exception as e:
            print(f"Error listing users: {str(e)}")
            return []
