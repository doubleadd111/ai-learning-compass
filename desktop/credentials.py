"""密钥只进入系统凭据管理器或当前进程内存。"""

class CredentialStore:
    SERVICE = "LearningCompass"

    def __init__(self, backend=None):
        if backend is None:
            import sys
            if sys.platform == "win32":
                from keyring.backends.Windows import WinVaultKeyring
                backend = WinVaultKeyring()
            else:
                import keyring
                backend = keyring
        self.backend = backend
        self.session_keys: dict[str, str] = {}

    def get(self, connection_id: str) -> str:
        if connection_id in self.session_keys:
            return self.session_keys[connection_id]
        try:
            return self.backend.get_password(self.SERVICE, connection_id) or ""
        except Exception:
            return ""

    def set(self, connection_id: str, key: str, remember: bool = True) -> bool:
        self.session_keys[connection_id] = key.strip()
        if remember:
            try:
                self.backend.set_password(self.SERVICE, connection_id, key.strip())
                return True
            except Exception:
                return False
        # 用户选择仅本次使用时，清除该配置先前保存的凭据。
        try:
            self.backend.delete_password(self.SERVICE, connection_id)
        except Exception:
            pass
        return False

    def delete(self, connection_id: str) -> None:
        self.session_keys.pop(connection_id, None)
        try:
            self.backend.delete_password(self.SERVICE, connection_id)
        except Exception:
            pass
