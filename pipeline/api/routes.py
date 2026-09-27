"""
API routes -- this is the ONE file that imports from both pipeline/ and
engine/. It's the integration seam. Owner: Samir (Day 3-4 per schedule).

Until engine.generator / engine.validator / engine.config_gen are real,
this will raise NotImplementedError -- that's expected and fine for
early development; Samir can build/test everything up to this call.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from shared.schema import FullResult, NetworkSpec
from pipeline.llm_layer import (
    RequirementParseError,
    RequirementServiceError,
    parse_requirements,
)
from engine.generator import generate_plan
from engine.validator import validate_plan
from engine.config_gen import generate_configs

router = APIRouter()


class DesignRequest(BaseModel):
    description: str  # plain-English requirements from the user


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
        validation = validate_plan(plan)
        configs = generate_configs(plan) if validation.overall_pass else []
        return FullResult(plan=plan, validation=validation, configs=configs)
    except NotImplementedError as e:
        # Expected during early development -- remove once engine/ is wired up.
        raise HTTPException(status_code=501, detail=str(e))
