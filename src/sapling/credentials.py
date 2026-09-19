"""Secrets stay in the user's OS credential vault, outside project state."""

import os

import keyring


def is_placeholder(key: str | None) -> bool:
    return not key or "placeholder" in key.lower() or key.lower() in {"your-api-key", "changeme"}


class CredentialVault:
    def set(self, provider: str, key: str):
        if provider not in {"openai", "openalex"}:
            raise ValueError("Unknown credential provider")
        if is_placeholder(key):
            raise ValueError("Enter an actual API key; placeholders are not stored")
        keyring.set_password("sapling", provider, key)

    def get(self, provider: str) -> str | None:
        try:
            saved = keyring.get_password("sapling", provider)
        except keyring.errors.KeyringError:
            saved = None
        value = saved or os.environ.get(f"{provider.upper()}_API_KEY")
        return None if is_placeholder(value) else value

    def delete(self, provider: str):
        try:
            keyring.delete_password("sapling", provider)
        except keyring.errors.PasswordDeleteError:
            pass
