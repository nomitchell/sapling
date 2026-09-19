"""Explicit capability policy. Commands are never classified by string inspection."""

from dataclasses import dataclass, field
from enum import StrEnum


class PermissionMode(StrEnum):
    ASK = "ask"
    BALANCED = "balanced"
    YOLO = "yolo"


class Capability(StrEnum):
    READ_WORKSPACE = "read_workspace"
    WRITE_WORKSPACE = "write_workspace"
    SEARCH_WEB = "search_web"
    FETCH_SOURCE = "fetch_source"
    EXECUTE_CONTAINER = "execute_container"
    NETWORK_CONTAINER = "network_container"
    EXECUTE_HOST = "execute_host"
    INSTALL_DEPENDENCIES = "install_dependencies"
    ACCESS_EXTERNAL_FILES = "access_external_files"


_ROUTINE = {
    Capability.READ_WORKSPACE,
    Capability.WRITE_WORKSPACE,
    Capability.SEARCH_WEB,
    Capability.FETCH_SOURCE,
    Capability.EXECUTE_CONTAINER,
}


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    requires_approval: bool
    reason: str
    category: str
    scope: str


@dataclass
class PermissionPolicy:
    """Grants belong to one project; scoped grants use ``category:scope``.

    A category-only grant applies to all scopes in that project. The caller must
    persist approval and check all requested capabilities before doing work.
    Host execution gives arbitrary code the user's OS rights. It is deliberately
    a capability of its own, including in balanced mode.
    """

    mode: PermissionMode | str = PermissionMode.BALANCED
    grants: set[str] = field(default_factory=set)

    def evaluate(self, category: Capability | str, scope: str = "project") -> PermissionDecision:
        mode = PermissionMode(self.mode)
        try:
            capability = Capability(category)
        except ValueError:
            return PermissionDecision(False, False, "Unknown capability; request rejected.", str(category), scope)
        granted = capability.value in self.grants or f"{capability.value}:{scope}" in self.grants
        allowed = mode == PermissionMode.YOLO or granted or (mode == PermissionMode.BALANCED and capability in _ROUTINE)
        if granted:
            reason = "An explicit project grant covers this capability."
        elif mode == PermissionMode.YOLO:
            reason = "Full autonomy is enabled for this project."
        elif allowed:
            reason = "Balanced mode allows this bounded capability."
        else:
            reason = "Explicit permission is required for this capability."
        return PermissionDecision(allowed, not allowed, reason, capability.value, scope)

    def evaluate_all(self, categories: list[Capability | str], scope: str = "project") -> list[PermissionDecision]:
        return [self.evaluate(category, scope) for category in categories]
