"""Wire types shared by HTTP, the worker and the published JSON schemas."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Key = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_.-]{1,100}$')]
Origin = Literal['manual','datadog','dynatrace']
State = Literal['investigation','mitigation','monitoring','resolved','closed']
Audience = Literal['operations','management','customer']
Provider = Literal['teams','slack']
Role = Literal['operator','coordinator','admin']
Impact = Literal['assessing','degraded','partially_unavailable','unavailable','recovered']

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Journey(Model):
    id: Key
    impact: Impact = 'assessing'

class Create(Model):
    title: Text
    origin: Origin = 'manual'
    severity: Key
    team: Key
    coordinator: Key
    journeys: list[Journey] = Field(default_factory=list)
    business_impact: Text | None = None
    affected_public: Text | None = None
    extent: Text | None = None

class Policy(Model):
    interval_minutes: int = Field(default=30, ge=1, le=10080)
    escalation_minutes: int = Field(default=10, ge=1, le=10080)
    responder: Key | None = None
    additional_teams: list[Key] = Field(default_factory=list)
    severity_roles: list[Role] = Field(default_factory=lambda:['coordinator','admin'])
    recovery_roles: list[Role] = Field(default_factory=lambda:['coordinator','admin'])
    closure_roles: list[Role] = Field(default_factory=lambda:['coordinator','admin'])

class OrgPolicy(Policy):
    severities: list[Key] = Field(default_factory=lambda:['P1','P2','P3','P4','P5'],min_length=1)

class Update(Model):
    no_change: bool = False
    situation: Text | None = None
    impact: Text | None = None
    actions: list[Text] = Field(default_factory=list)
    next_step: Text | None = None
    owner: Key | None = None
    recovery_at: AwareDatetime | None
    next_update: AwareDatetime | None = None
    fact_at: AwareDatetime
    evidence: list[UUID] = Field(default_factory=list)
    @model_validator(mode='after')
    def substantive(self):
        if not self.no_change and not (self.situation and self.impact):
            raise ValueError('Provide situation and impact, or explicitly select no_change')
        return self

class Transition(Model):
    state: State
    reason: Text
    confirmation: UUID | None = None

class Room(Model):
    provider: Provider
    room_id: Key
    state: Literal['requested','open','closed']
    confirmation: UUID | None = None

class CloseRequest(Model):
    target: Literal['incident','room']
    provider: Provider | None = None
    room_id: Key | None = None

class Severity(Model):
    severity: Key
    reason: Text

class External(Model):
    provider: Literal['servicenow','jira_service_management']
    identifier: Key
    sync_state: Literal['pending','synced','failed']
    ticket_state: Text | None = None

class Duplicate(Model):
    principal_id: UUID

class Correction(Model):
    event_id: UUID
    correction: Text
    fact_at: AwareDatetime
    evidence: list[UUID] = Field(default_factory=list)

class Pause(Model):
    reason: Text
    until: AwareDatetime

class Draft(Model):
    audience: Audience
    text: Text

class Approval(Model):
    publication_id: UUID

class SimMessage(Model):
    provider: Provider
    room_id: Key
    update: Update | None = None
    message: Text | None = None

class Forecast(Model):
    at: AwareDatetime | None
    author: str | None
    recorded_at: AwareDatetime | None
    status: Literal['unknown','confirmed','overdue']

class Incident(Create):
    id: UUID
    tenant: str
    version: int
    state: State
    duplicate_of: UUID | None
    rooms: list[Room]
    references: list[External]
    last_update: AwareDatetime | None
    next_update: AwareDatetime | None
    forecast: Forecast
    policy_override: Policy | None
    pause: Pause | None

class Event(Model):
    id: UUID
    incident_id: UUID
    version: int
    kind: str
    author: str
    origin: str
    fact_at: AwareDatetime
    recorded_at: AwareDatetime
    data: dict

class Publication(Model):
    id: UUID
    audience: Audience
    text: str
    approved_by: str
    published_at: AwareDatetime
