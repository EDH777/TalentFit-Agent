import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Form, Input, InputNumber, Select, Space, Tag, Typography } from 'antd'
import type { Employee, Requirements, RoleSlot, StaffingProject, WorkDay } from '../../types/staffing'
import { staffingApi, staffingError } from '../../services/staffing'
import { exclusiveEnd, inclusiveEnd } from '../../services/datetime'

interface FormValues {
  description: string
  start_date: string
  end_date_inclusive: string
  slots: RoleSlot[]
}

const emptySlot = (workDates: string[] = []) => ({ role_name: '', units_per_day: 40, work_dates: workDates, required_skills: [], preferred_skills: [] })

function requirementErrors(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: { code?: string; message?: string; errors?: { field: string; message: string }[] } } } })?.response?.data?.detail
  if (detail?.errors?.length) return `需求未通过校验：${detail.errors.map(item => `${item.field}：${item.message}`).join('；')}`
  return staffingError(error)
}

export default function RequirementsEditor({ project, employees, disabled, onSaved, draft }: { project: StaffingProject; employees: Employee[]; disabled: boolean; onSaved: () => void; draft?: Requirements | null }) {
  const [form] = Form.useForm<FormValues>()
  const [calendar, setCalendar] = useState<WorkDay[]>([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const previousWorkDays = useRef<string[]>([])
  const current = project.requirements
  const start = Form.useWatch('start_date', form)
  const endInclusive = Form.useWatch('end_date_inclusive', form)
  const slots = Form.useWatch('slots', form) || []
  const validRange = Boolean(start && endInclusive && start <= endInclusive)
  const workDays = validRange ? calendar.filter(day => day.is_workday).map(day => day.work_date) : []
  const hasPlans = Boolean(project.latest_plan_state && project.latest_plan_state !== 'rejected')

  useEffect(() => {
    form.setFieldsValue({
      description: project.description,
      start_date: (draft ?? current)?.start_date || project.start_date,
      end_date_inclusive: inclusiveEnd((draft ?? current)?.end_date || project.end_date),
      slots: (draft ?? current)?.slots?.length ? (draft ?? current)!.slots : [emptySlot()],
    })
  }, [project.id, project.version, current, draft, form])

  useEffect(() => {
    if (!validRange) { setCalendar([]); return }
    let ignore = false
    staffingApi.get<{ days: WorkDay[] }>('/work-calendar', { start_date: start, end_date: exclusiveEnd(endInclusive) })
      .then(data => { if (!ignore) setCalendar(data.days) })
      .catch(e => { if (!ignore) setError(staffingError(e)) })
    return () => { ignore = true }
  }, [start, endInclusive, validRange])

  useEffect(() => {
    if (!workDays.length) return
    const previous = previousWorkDays.current
    previousWorkDays.current = workDays
    if (!previous.length) {
      // First load: slots without an explicit subset follow the whole range.
      const rows = form.getFieldValue('slots') as RoleSlot[] | undefined
      if (rows?.some(slot => !slot?.work_dates?.length)) {
        form.setFieldsValue({ slots: rows.map(slot => ({ ...slot, work_dates: slot?.work_dates?.length ? slot.work_dates : workDays })) })
      }
      return
    }
    const rows = form.getFieldValue('slots') as RoleSlot[] | undefined
    if (!rows?.length) return
    const moved = rows.map(slot => {
      const selected = slot?.work_dates || []
      const followedRange = selected.length === previous.length && previous.every(day => selected.includes(day))
      const next = followedRange ? workDays : selected.filter(day => workDays.includes(day))
      return { ...slot, work_dates: next.length ? next : workDays }
    })
    form.setFieldsValue({ slots: moved })
  }, [workDays.join('|'), form])

  const setSlotDays = (index: number, value: string[]) => {
    form.setFieldValue(['slots', index, 'work_dates'], value)
  }

  const save = async () => {
    setError('')
    let values: FormValues
    try {
      values = await form.validateFields()
    } catch (validation) {
      const first = (validation as { errorFields?: { errors: string[] }[] })?.errorFields?.[0]?.errors?.[0]
      setError(first ? `请先修正表单：${first}` : '请先补全必填项')
      return
    }
    if (values.start_date > values.end_date_inclusive) { setError('结束日期不能早于开始日期'); return }
    if (!workDays.length) { setError('所选区间没有公司工作日，请调整日期后重试'); return }
    setSaving(true)
    try {
      const { description, ...rest } = values
      const employeeIds = employees.filter(employee => employee.employment_status === 'active').map(employee => employee.id)
      await staffingApi.patch(`/projects/${project.id}/requirement`, {
        expected_version: project.version,
        expected_requirements_revision: project.requirements_revision,
        confirmed: true,
        description,
        requirements: {
          ...rest,
          end_date: exclusiveEnd(rest.end_date_inclusive),
          work_dates: workDays,
          employee_ids: employeeIds,
          schema_version: '1',
          required_employee_ids: [],
          excluded_employee_ids: [],
          slots: rest.slots.map(slot => ({
            ...slot,
            work_dates: slot.work_dates?.length ? slot.work_dates : workDays,
            required_skills: slot.required_skills || [],
            preferred_skills: slot.preferred_skills || [],
          })),
        },
      })
      onSaved()
    } catch (e) { setError(requirementErrors(e)) } finally { setSaving(false) }
  }

  return <Form form={form} layout="vertical" disabled={disabled} className="staffing-requirements" scrollToFirstError={{ behavior: 'smooth', block: 'center' }}>
    {error && <Alert type="error" showIcon message={error} closable onClose={() => setError('')} />}
    {hasPlans && <Alert type="info" showIcon message="修改需求会重新生成需求版本，已有方案将变为历史版本，需要重新生成推荐。" />}
    <Form.Item label="项目目标" name="description" rules={[{ required: true, message: '请说明项目要完成什么' }]}><Input.TextArea rows={2} maxLength={20000} /></Form.Item>
    <div className="staffing-date-pair">
      <Form.Item label="开始日期" name="start_date" rules={[{ required: true, message: '请选择开始日期' }]}><Input type="date" /></Form.Item>
      <Form.Item label="结束日期（含）" name="end_date_inclusive" rules={[{ required: true, message: '请选择结束日期' }]}><Input type="date" /></Form.Item>
    </div>
    <div className="staffing-workdays">
      <Typography.Text type="secondary">项目工作日由公司日历自动确定：</Typography.Text>
      {!validRange && <Typography.Text type="warning">请先选择有效日期区间。</Typography.Text>}
      {validRange && !workDays.length && <Typography.Text type="warning">该区间没有公司工作日，请调整日期。</Typography.Text>}
      {workDays.length > 0 && <Space wrap size={4}>{workDays.map(day => <Tag key={day}>{day}</Tag>)}</Space>}
      {workDays.length > 0 && <Typography.Text type="secondary">共 {workDays.length} 个工作日，节假日与周末自动排除。</Typography.Text>}
    </div>
    <Form.List name="slots" rules={[{ validator: async (_, rows) => { if (!rows?.length) throw new Error('请添加至少一个岗位') } }]}>{(fields, { add, remove }, { errors }) => <section>
      <div className="staffing-section-heading"><Typography.Text strong>项目岗位</Typography.Text><span className="staffing-muted">每个岗位由 Agent 推荐一名员工；参与日期默认跟随项目，可取消不参与的日期</span></div>
      {fields.map(field => {
        const selected = slots[field.name]?.work_dates || []
        return <fieldset className="staffing-slot" key={field.key}>
          <legend>岗位 {field.name + 1}</legend>
          <div className="staffing-slot-grid">
            <Form.Item label="岗位名称" name={[field.name, 'role_name']} rules={[{ required: true, message: '请输入岗位名称' }, { validator: async (_, value) => { const names = new Set(employees.filter(employee => employee.employment_status === 'active').map(employee => employee.display_name.trim())); if (typeof value === 'string' && names.has(value.trim())) throw new Error('岗位名称不能填写员工姓名，请填写岗位或职责名称') } }]}><Input placeholder="例如：后端开发工程师" /></Form.Item>
            <Form.Item label="每日投入比例（%）" name={[field.name, 'units_per_day']} extra="100% 表示一个标准工作日" rules={[{ required: true, message: '请输入投入比例' }]}><InputNumber min={1} max={100} precision={0} /></Form.Item>
          </div>
          <Form.Item label={`参与工作日（已选 ${selected.length}/${workDays.length} 天）`} name={[field.name, 'work_dates']} required
            extra="默认按项目全部工作日，取消勾选表示该岗位不参与这些日期"
            rules={[{ validator: async (_, value) => { if (!value?.length) throw new Error('每个岗位至少保留一个参与工作日') } }]}>
            <Checkbox.Group options={workDays.map(day => ({ label: day.slice(5), value: day }))} />
          </Form.Item>
          <Space size={8} className="staffing-slot-days">
            <Button type="link" size="small" disabled={disabled} onClick={() => setSlotDays(field.name, workDays)}>全选</Button>
            <Button type="link" size="small" disabled={disabled} onClick={() => setSlotDays(field.name, [])}>清空</Button>
          </Space>
          <Form.Item label="必备技能" name={[field.name, 'required_skills']}><Select mode="tags" tokenSeparators={[',', '，']} placeholder="例如：Java、Spring Boot" /></Form.Item>
          <Form.Item label="偏好技能" name={[field.name, 'preferred_skills']}><Select mode="tags" tokenSeparators={[',', '，']} placeholder="可选" /></Form.Item>
          {!disabled && fields.length > 1 && <Button danger type="text" onClick={() => remove(field.name)}>移除此岗位</Button>}
        </fieldset>
      })}
      <Form.ErrorList errors={errors} />{!disabled && fields.length < 10 && <Button block onClick={() => add({ ...emptySlot(workDays), required_skills: [], preferred_skills: [] })}>添加岗位</Button>}
    </section>}</Form.List>
    {!disabled && <Space wrap className="staffing-actions"><Button type="primary" loading={saving} disabled={!workDays.length} onClick={() => void save()}>保存并确认需求</Button><span className="staffing-muted">保存后即可生成推荐</span></Space>}
  </Form>
}
