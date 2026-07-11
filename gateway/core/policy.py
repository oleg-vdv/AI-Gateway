"""Policy Engine (F4): mask / block / allow по типу сущности,
с переопределениями по каналу и группе пользователей. Fail-closed.

Действие `allow` = осознанная трансграничная передача ПДн (ст. 16
Закона № 94-V): применяется только если в правиле явно указано
основание (allow_basis), и логируется отдельно.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from gateway.models import Action, Channel, Detection, EntityType, Policy, PolicyRule


def default_policy() -> Policy:
    return Policy(
        name="default",
        rules=[
            PolicyRule(entity_type=EntityType.IIN, action=Action.MASK),
            PolicyRule(entity_type=EntityType.BIN, action=Action.MASK),
            PolicyRule(entity_type=EntityType.PERSON, action=Action.MASK),
            PolicyRule(entity_type=EntityType.IBAN, action=Action.MASK),
            PolicyRule(entity_type=EntityType.CARD, action=Action.MASK),
            # Секреты по умолчанию блокируются: маскирование API-ключа в
            # промпте почти всегда значит, что ключ уже надо ротировать.
            PolicyRule(entity_type=EntityType.SECRET, action=Action.BLOCK),
            PolicyRule(entity_type=EntityType.AMOUNT, action=Action.MASK),
            # Маркеры коммерческой тайны: маскирование бессмысленно
            # (гриф — не значение), документ с грифом не должен уходить.
            PolicyRule(entity_type=EntityType.CONFIDENTIAL, action=Action.BLOCK),
        ],
        fail_closed=True,
    )


class PolicyEngine:
    def __init__(self, policy_path: Path):
        self._path = policy_path
        self._policy = self._load()

    @property
    def policy(self) -> Policy:
        return self._policy

    def _load(self) -> Policy:
        if self._path.exists():
            try:
                return Policy.from_dict(json.loads(self._path.read_text()))
            except Exception:
                # повреждённая политика -> безопасный дефолт (fail-safe)
                return default_policy()
        return default_policy()

    def save(self, policy: Policy) -> None:
        self._policy = copy.deepcopy(policy)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(policy.to_dict(), ensure_ascii=False, indent=2)
        )

    def action_for(
        self, entity_type: EntityType, channel: Channel, group: str
    ) -> tuple[Action, str | None]:
        """Действие для типа сущности с учётом overrides.

        Приоритет: группа > канал > базовое правило > mask (fail-safe).
        Возвращает (действие, основание_для_allow).
        """
        base_rule = next(
            (r for r in self._policy.rules if r.entity_type == entity_type), None
        )
        action = base_rule.action if base_rule else Action.MASK
        basis = base_rule.allow_basis if base_rule else None

        channel_map = self._policy.channel_overrides.get(channel.value, {})
        if entity_type.value in channel_map:
            action = channel_map[entity_type.value]

        group_map = self._policy.group_overrides.get(group, {})
        if entity_type.value in group_map:
            action = group_map[entity_type.value]

        # allow без зафиксированного основания недопустим -> деградируем в mask
        if action == Action.ALLOW and not basis:
            action = Action.MASK
        return action, basis

    def evaluate(
        self, detections: list[Detection], channel: Channel, group: str
    ) -> tuple[dict[str, Action], list[Detection], bool, str | None]:
        """Применить политику к найденным сущностям.

        Возвращает:
          actions      — {entity_type: действие} по всем встреченным типам;
          to_mask      — сущности, подлежащие токенизации;
          blocked      — True, если хоть один тип требует блокировки;
          allow_basis  — основание, если что-то пропущено как allow.
        """
        actions: dict[str, Action] = {}
        to_mask: list[Detection] = []
        blocked = False
        allow_basis: str | None = None
        for det in detections:
            action, basis = self.action_for(det.entity_type, channel, group)
            actions[det.entity_type.value] = action
            if action == Action.BLOCK:
                blocked = True
            elif action == Action.MASK:
                to_mask.append(det)
            elif action == Action.ALLOW:
                allow_basis = basis
        return actions, to_mask, blocked, allow_basis
