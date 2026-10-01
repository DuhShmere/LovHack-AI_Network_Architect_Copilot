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
    explain_design,
    parse_requirements,
    refine_requirements,
)
from engine.generator import PlanGenerationError, generate_plan
from engine.validator import validate_plan
from engine.config_gen import generate_configs
from engine.demo import SabotageError, SabotageInfo, break_design, list_sabotages
from engine.simulator import ResilienceReport, SimulationError, SimulationReport, resilience, simulate
from engine.bom import BillOfMaterials, bill_of_materials
from engine.plan_diff import diff_plans

router = APIRouter()


def _not_blank(v: str, field: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError(f"{field} must not be empty")
    return v


class DesignRequest(BaseModel):
    description: str  # plain-English requirements from the user

    @field_validator("description")
    @classmethod
    def description_must_not_be_blank(cls, v: str) -> str:
        return _not_blank(v, "description")


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


def _call_llm(fn, *args):
    try:
        return fn(*args)
    except RequirementParseError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RequirementServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))


def _design_from_spec(spec: NetworkSpec) -> FullResult:
    try:
        plan = generate_plan(spec)
    except PlanGenerationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    validation = validate_plan(plan)
    configs = generate_configs(plan) if validation.overall_pass else []
    return FullResult(plan=plan, validation=validation, configs=configs)


@router.post("/design", response_model=FullResult)
def design_network(req: DesignRequest) -> FullResult:
    return _design_from_spec(_call_llm(parse_requirements, req.description))


class RefineRequest(BaseModel):
    plan: NetworkPlan  # the current design (its spec is what gets changed)
    change: str  # plain-English change, e.g. "make it 150 users"

    @field_validator("change")
    @classmethod
    def change_must_not_be_blank(cls, v: str) -> str:
        return _not_blank(v, "change")


class RefineResponse(BaseModel):
    result: FullResult
    changes: list[str]  # what the change did to the design, in plain sentences


@router.post("/refine", response_model=RefineResponse)
def refine_design(req: RefineRequest) -> RefineResponse:
    """Apply a plain-English change to an existing design and redesign."""
    spec = _call_llm(refine_requirements, req.plan.spec, req.change)
    result = _design_from_spec(spec)
    return RefineResponse(result=result, changes=diff_plans(req.plan, result.plan))


class ExplainRequest(BaseModel):
    result: FullResult
    question: str

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, v: str) -> str:
        return _not_blank(v, "question")


class ExplainResponse(BaseModel):
    answer: str


@router.post("/explain", response_model=ExplainResponse)
def explain(req: ExplainRequest) -> ExplainResponse:
    """Answer a question about a design, grounded in the design itself."""
    return ExplainResponse(answer=_call_llm(explain_design, req.result, req.question))


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
    """Break a valid plan (or the configs generated from it) one named way
    and re-validate, to show the validator catching it live."""
    try:
        broken, what_changed, validation = break_design(req.plan, req.sabotage)
    except SabotageError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return BreakResponse(plan=broken, what_changed=what_changed, validation=validation)


class SimulateRequest(BaseModel):
    plan: NetworkPlan
    failed: list[str] = []  # node_ids to treat as down


@router.post("/simulate", response_model=SimulationReport)
def simulate_traffic(req: SimulateRequest) -> SimulationReport:
    """Walk traffic between every VLAN and to the internet through the
    generated configs, with the given devices failed."""
    try:
        return simulate(req.plan, req.failed)
    except SimulationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/resilience", response_model=ResilienceReport)
def resilience_report(req: PlanRequest) -> ResilienceReport:
    """Try every single-device failure and report single points of failure."""
    try:
        return resilience(req.plan)
    except SimulationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/bom", response_model=BillOfMaterials)
def bom(req: PlanRequest) -> BillOfMaterials:
    """Budgetary bill of materials for the design's devices."""
    return bill_of_materials(req.plan)
