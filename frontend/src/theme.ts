import type { ThemeConfig } from 'antd'

export const interfaceFont = '"Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif'

export const appTheme: ThemeConfig = {
  token: {
    colorPrimary: '#315cce', colorInfo: '#315cce',
    colorText: '#243247', colorTextSecondary: '#59697e', colorTextPlaceholder: '#68788c',
    colorBorder: '#dce3ed', colorBorderSecondary: '#e6ebf2', colorBgLayout: '#f4f6fa',
    fontFamily: interfaceFont, borderRadius: 6,
  },
  components: {
    Layout: { headerBg: '#ffffff', siderBg: '#14253b', triggerBg: '#102036' },
    Menu: {
      darkItemBg: '#14253b', darkSubMenuItemBg: '#14253b', darkItemColor: '#c8d3e3',
      darkItemHoverBg: '#203956', darkItemSelectedBg: '#315cce', darkItemSelectedColor: '#ffffff',
      itemHeight: 36,
    },
    Card: { paddingLG: 16 },
    Table: { headerBg: '#f5f7fb', headerColor: '#46576e', rowHoverBg: '#f2f6ff' },
  },
}
