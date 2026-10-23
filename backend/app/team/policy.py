"""Recorded policy decisions for team tools; execution rechecks membership."""
from dataclasses import dataclass


@dataclass(frozen=True)
class TurnTeam:
    team_id: int
    agent_id: int
    is_lead: bool
    lead_auto: bool
    origin: str = 'person'
    depth: int = 0
    delegation_enabled: bool = True


def automatic(tool, mode, team, arguments):
    if not tool.startswith('mcp__team__'):
        return None
    if team is None:
        return 'denied', 'no_team'
    name = tool.removeprefix('mcp__team__')
    if name in ('list_agents', 'list_rooms'):
        return 'allowed', 'team_read'
    if name == 'ask_agent':
        if not team.delegation_enabled:
            return 'denied', 'delegation_disabled'
        if arguments.get('mode') == 'consult':
            return 'allowed', 'team_consult'
        if mode == 'plan':
            return 'denied', 'plan_mode'
        if team.origin in ('outside', 'webhook'):
            return None
    elif name in ('create_agent', 'create_room'):
        if mode == 'plan':
            return 'denied', 'plan_mode'
        if team.origin in ('outside', 'webhook'):
            return 'denied', 'outside_origin'
        if not team.is_lead:
            return 'denied', 'lead_only'
    elif name == 'say_in_room':
        return 'allowed', 'team_room_post'
    else:
        return 'denied', 'unknown_team_tool'
    if team.is_lead and team.lead_auto:
        return 'allowed', 'team_policy'
    return None
