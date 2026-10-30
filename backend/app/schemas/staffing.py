"""Small versioned, validated contracts shared by staffing APIs and tools."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RoleSlot(Contract):
    slot_id: str | None = None
    role_name: str = Field(min_length=1, max_length=120)
    units_per_day: int = Field(ge=1, le=100, strict=True)
    work_dates: list[date] = Field(min_length=1, max_length=90)
    required_skills: list[str] = Field(default_factory=list, max_length=100)
    preferred_skills: list[str] = Field(default_factory=list, max_length=100)


class ConfirmedRequirements(Contract):
    schema_version: Literal['1'] = '1'
    start_date: date
    end_date: date
    work_dates: list[date] = Field(min_length=1, max_length=90)
    slots: list[RoleSlot] = Field(min_length=1, max_length=10)
    employee_ids: list[str] = Field(min_length=1, max_length=100)
    required_employee_ids: list[str] = Field(default_factory=list, max_length=10)
    excluded_employee_ids: list[str] = Field(default_factory=list, max_length=100)
    revision: int = Field(default=1, ge=1)

    @model_validator(mode='after')
    def validate_scope_dates(self):
        if not 0 < (self.end_date - self.start_date).days <= 90:
            raise ValueError('项目日期范围必须为1至90天，结束日期不计入')
        dates = set(self.work_dates)
        if len(dates) != len(self.work_dates) or any(not self.start_date <= d < self.end_date for d in dates):
            raise ValueError('工作日重复或超出项目日期范围')
        if len(set(self.employee_ids)) != len(self.employee_ids):
            raise ValueError('员工范围不能重复')
        if not set(self.required_employee_ids).issubset(self.employee_ids) or not set(self.excluded_employee_ids).issubset(self.employee_ids):
            raise ValueError('指定与排除员工必须属于确认范围')
        if set(self.required_employee_ids) & set(self.excluded_employee_ids):
            raise ValueError('指定员工不能同时被排除')
        for slot in self.slots:
            if len(set(slot.work_dates)) != len(slot.work_dates) or not set(slot.work_dates).issubset(dates):
                raise ValueError('名额工作日必须属于项目工作日且不能重复')
        identities = [slot.slot_id for slot in self.slots if slot.slot_id]
        if len(identities) != len(set(identities)):
            raise ValueError('名额ID不能重复')
        return self


def assign_slot_ids(requirements, existing_ids=None):
    from app.models.staffing import new_id
    from app.services.staffing.common import fail
    result = requirements.model_copy(deep=True)
    for slot in result.slots:
        if slot.slot_id and (existing_ids is None or slot.slot_id not in existing_ids):
            fail(422, 'INVALID_SLOT', '名额ID由后端生成，更新时只能保留原有名额ID')
        if not slot.slot_id:
            slot.slot_id = new_id()
    return result


class RequirementDraft(Contract):
    schema_version: Literal['1'] = '1'
    requirements: dict = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    changes: dict = Field(default_factory=dict)


class Assignment(Contract):
    employee_id: str
    slot_id: str
    units_per_day: int | None = Field(default=None, ge=1, le=100)
    start_date: date | None = None
    end_date: date | None = None
    work_dates: list[date] = Field(default_factory=list)


class AllocationAdjustRequest(Contract):
    expected_version: int = Field(ge=1, strict=True)
    units_per_day: int = Field(ge=1, le=100, strict=True)
    work_dates: list[date] = Field(min_length=1, max_length=90)
    basis: str = Field(default='', max_length=2000)

