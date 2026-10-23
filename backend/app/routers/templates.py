"""Ready-made agent templates (`GET /templates`).

Read-only and local: the bundled catalog (app/templates/catalog.py). Nothing
here creates an agent, calls a model or grants anything; the client fills the
new-agent form from a template and the person creates the agent.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.services.local_access_policy import require_local_access
from app.templates import catalog

router = APIRouter(
    prefix="/templates",
    tags=["Templates"],
    dependencies=[Depends(require_local_access)],
)


class TemplateSkillResponse(BaseModel):
    name: str
    description: str
    body: str


class TemplateResponse(BaseModel):
    id: str
    name: str
    role: str
    category: str
    description: str
    instructions: str
    color: str
    example_requests: list[str]
    requires: list[str]
    available: bool
    starter_notes: str | None
    skills: list[TemplateSkillResponse]


class TemplateListResponse(BaseModel):
    version: int
    categories: list[str]
    templates: list[TemplateResponse]


def _response(template: catalog.Template) -> TemplateResponse:
    return TemplateResponse(
        id=template.id,
        name=template.name,
        role=template.role,
        category=template.category,
        description=template.description,
        instructions=template.instructions,
        color=template.color,
        example_requests=list(template.example_requests),
        requires=list(template.requires),
        available=template.available,
        starter_notes=template.starter_notes,
        skills=[TemplateSkillResponse(name=s.name, description=s.description, body=s.body) for s in template.skills],
    )


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    q: str | None = Query(default=None, max_length=200),
    category: str | None = Query(default=None, max_length=40),
):
    if category and category not in catalog.CATEGORIES:
        raise HTTPException(status_code=422, detail={"error_code": "unknown_category", "message": "Unknown category."})
    return TemplateListResponse(
        version=catalog.CATALOG_VERSION,
        categories=list(catalog.CATEGORIES),
        templates=[_response(t) for t in catalog.search(q, category)],
    )


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(template_id: str):
    template = catalog.get(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail={"error_code": "unknown_template", "message": "No such template."})
    return _response(template)
