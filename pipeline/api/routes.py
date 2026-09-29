"""
API routes -- this is the ONE file that imports from both pipeline/ and
engine/. It's the integration seam. Owner: Samir (Day 3-4 per schedule).
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from shared.schema import FullResult, NetworkPlan, NetworkSpec, ValidationReport
from pipeline.llm_layer import (
    RequirementParseError,
    RequirementServiceError,
    parse_requirements,
)
from engine.generator import PlanGenerationError, generate_plan
from engine.validator import validate_plan
from engine.config_gen import generate_configs
from engine.demo import SabotageError, SabotageInfo, list_sabotages, sabotage_plan

router = APIRouter()


class DesignRequest(BaseModel):
    description: str  # plain-English requirements from the user

    @field_validator("description")
    @classmethod
    def description_must_not_be_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("description must not be empty")
        return v


@router.post("/parse", response_model=NetworkSpec)
def parse_only(req: DesignRequest) -> NetworkSpec:
    """LLM-extraction only, no engine/ dependency -- lets the dashboard and
    tests exercise the real parsing step while engine/ is still stubbed."""
    try:
        return parse_requirements(req.description)
    except RequirementParseError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RequirementServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/design", response_model=FullResult)
def design_network(req: DesignRequest) -> FullResult:
    try:
        spec = parse_requirements(req.description)
    except RequirementParseError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RequirementServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        plan = generate_plan(spec)
    except PlanGenerationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    validation = validate_plan(plan)
    configs = generate_configs(plan) if validation.overall_pass else []
    return FullResult(plan=plan, validation=validation, configs=configs)


class PlanRequest(BaseModel):
    plan: NetworkPlan


class BreakRequest(BaseModel):
    plan: NetworkPlan
    sabotage: str


class BreakResponse(BaseModel):
    plan: NetworkPlan
    what_changed: str
    validation: ValidationReport


@router.post("/demo/sabotages", response_model=list[SabotageInfo])
def demo_sabotages(req: PlanRequest) -> list[SabotageInfo]:
    """Which 'break it' buttons apply to this plan, for the dashboard to show."""
    return list_sabotages(req.plan)


@router.post("/demo/break", response_model=BreakResponse)
def demo_break(req: BreakRequest) -> BreakResponse:
    """Break a valid plan one named way and re-validate, to show the
    validator catching it live."""
    try:
        broken, what_changed = sabotage_plan(req.plan, req.sabotage)
    except SabotageError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return BreakResponse(plan=broken, what_changed=what_changed, validation=validate_plan(broken))
