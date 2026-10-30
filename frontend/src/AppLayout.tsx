import { useEffect, useState } from 'react'
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Button, ConfigProvider, Layout, Menu, Modal, Result, Space, Spin, Tag, Typography } from 'antd'
import { DashboardOutlined, MessageOutlined, TeamOutlined, LogoutOutlined } from '@ant-design/icons'
import api from './services/api'
import type { User } from './types'
import { IngestionProvider, useIngestion } from './components/ingestion/IngestionContext'
import IngestionNotice from './components/ingestion/IngestionNotice'

const { Header, Sider, Content } = Layout

export function useUser(): User | null {
  try { return JSON.parse(localStorage.getItem('talentfit_user') || 'null') as User | null }
  catch { return null }
}

export function RequireAuth() {
  const [status, setStatus] = useState<'loading' | 'ready' | 'unauthorized' | 'error'>('loading')
  const location = useLocation()
  useEffect(() => {
    if (!localStorage.getItem('talentfit_token')) { setStatus('unauthorized'); return }
    api.get<User>('/auth/me').then(({ data }) => {
      localStorage.setItem('talentfit_user', JSON.stringify(data)); setStatus('ready')
    }).catch((error) => setStatus(error.response?.status === 401 ? 'unauthorized' : 'error'))
  }, [])
  if (status === 'loading') return <div className="loading-screen"><Spin tip="正在验证登录状态" /></div>
  if (status === 'unauthorized') return <Navigate to="/login" replace state={{ from: location }} />
  if (status === 'error') return <Result status="warning" title="暂时无法连接服务" extra={<Button onClick={() => window.location.reload()}>重新连接</Button>} />
  return <Outlet />
}

export function RequireHr() {
  return useUser()?.role === 'hr' ? <Outlet /> : <Navigate to="/" replace />
}

export function AppLayout() {
  const user = useUser()
  const token = localStorage.getItem('talentfit_token')
  return <IngestionProvider key={`${user?.id}:${user?.role}:${token}`} token={token} canUpload={user?.role === 'hr'}><AuthenticatedLayout /></IngestionProvider>
}

function AuthenticatedLayout() {
  const user = useUser()
  const { uploading, dispose } = useIngestion()
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const [logoutOpen, setLogoutOpen] = useState(false)
  const logout = () => { dispose(); localStorage.removeItem('talentfit_token'); localStorage.removeItem('talentfit_user'); navigate('/login') }
  const items = [
    { key: '/', icon: <DashboardOutlined />, label: '工作台' },
    { key: '/chat', icon: <MessageOutlined />, label: '人才助手' },
      { key: 'staffing-menu', icon: <TeamOutlined />, label: '项目组队', children: [
      { key: '/staffing', label: '项目列表' },
      { key: '/staffing/employees', label: '人员准备' },
      { key: '/staffing/system', label: '系统准备' },
    ] },
    ...(user?.role === 'hr' ? [{ key: '/candidates', icon: <TeamOutlined />, label: '人才知识库' }] : []),
  ]
  return <ConfigProvider componentSize="small" theme={{ token: { fontSize: 13, controlHeightSM: 26, borderRadius: 6 } }}><Layout className="app-shell">
    <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed} theme="dark" breakpoint="lg">
      <div className="brand" title="企业人才匹配助手"><img className="brand-logo" src="/icon.png" alt="企业人才匹配助手" />{!collapsed && <span>TalentFit</span>}</div>
      <Menu theme="dark" mode="inline" defaultOpenKeys={['staffing-menu']} selectedKeys={[location.pathname.startsWith('/candidates') || location.pathname.startsWith('/knowledge') ? '/candidates' : location.pathname.startsWith('/staffing/employees') ? '/staffing/employees' : location.pathname.startsWith('/staffing/system') ? '/staffing/system' : location.pathname.startsWith('/staffing') ? '/staffing' : location.pathname]} items={items} onClick={({ key }) => navigate(key)} />
    </Sider>
    <Layout>
      <Header className="topbar">
        <Typography.Text strong>企业人才匹配助手</Typography.Text>
        <Space><Tag color={user?.role === 'hr' ? 'blue' : 'gold'}>{user?.role === 'hr' ? '人事' : '管理员'}</Tag><Typography.Text className="account-name" title={user?.username}>{user?.username}</Typography.Text>
          <Button type="text" icon={<LogoutOutlined />} onClick={() => uploading ? setLogoutOpen(true) : logout()}>退出</Button>
        </Space>
      </Header>
      <Content className="page-content"><IngestionNotice /><Outlet /></Content>
    </Layout>
    <Modal title="仍有简历正在入库" open={logoutOpen} onCancel={() => setLogoutOpen(false)} onOk={logout} cancelText="继续入库" okText="退出并中断" okButtonProps={{ danger: true }}>
      <Typography.Paragraph>退出会中断未完成的处理，已入库的资料会保留。再次登录后请刷新知识库确认结果。</Typography.Paragraph>
    </Modal>
  </Layout></ConfigProvider>
}
