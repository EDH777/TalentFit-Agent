import { useState } from 'react'
import { Alert, Button, Card, Form, Input, Typography } from 'antd'
import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { useLocation, useNavigate } from 'react-router-dom'
import api from '../services/api'
import type { User } from '../types'

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname || '/'

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true); setError('')
    try {
      const { data } = await api.post<{ access_token: string; user: User }>('/auth/login', values)
      localStorage.setItem('talentfit_token', data.access_token)
      localStorage.setItem('talentfit_user', JSON.stringify(data.user))
      navigate(from, { replace: true })
    } catch (e: any) {
      setError(e.response?.data?.detail || '登录失败，请检查账号和密码')
    } finally { setLoading(false) }
  }

  return <div className="login-page">
    <Card className="login-card" bordered={false}>
      <div className="login-mark"><img src="/icon.png" alt="企业人才匹配助手" /></div>
      <Typography.Title level={2}>企业人才助手</Typography.Title>
      <Typography.Paragraph type="secondary">登录后管理人才资料，或与 Agent 对话。</Typography.Paragraph>
      {error && <Alert className="form-alert" type="error" showIcon message={error} />}
      <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
        <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }]}>
          <Input prefix={<UserOutlined />} placeholder="请输入用户名" size="large" />
        </Form.Item>
        <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}>
          <Input.Password prefix={<LockOutlined />} placeholder="请输入密码" size="large" />
        </Form.Item>
        <Button type="primary" htmlType="submit" size="large" block loading={loading}>登录</Button>
      </Form>
    </Card>
  </div>
}
