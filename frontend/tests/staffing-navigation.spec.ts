import { test, expect } from '@playwright/test'

for (const role of ['hr', 'admin']) {
  test(`${role}从项目组队子菜单进入人员准备和系统准备并保持选中`, async ({ page }) => {
    await page.addInitScript(role => {
      localStorage.setItem('talentfit_token', 'synthetic')
      localStorage.setItem('talentfit_user', JSON.stringify({ id: 1, username: role, role }))
    }, role)
    await page.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname
      const body = path.endsWith('/auth/me') ? { id: 1, username: role, role }
        : path.endsWith('/setup') ? { revision: 1, bootstrap_closed: false } : []
      return route.fulfill({ json: body })
    })
    await page.goto('/staffing')
    const menu = page.getByRole('menu')
    await expect(menu.getByRole('menuitem', { name: '项目列表', exact: true })).toBeVisible()
    await menu.getByRole('menuitem', { name: '人员准备', exact: true }).click()
    await expect(page).toHaveURL(/\/staffing\/employees$/)
    await expect(page.getByRole('heading', { name: '人员准备', exact: true })).toBeVisible()
    await expect(menu.locator('.ant-menu-item-selected')).toHaveText('人员准备')
    await page.reload()
    await expect(menu.locator('.ant-menu-item-selected')).toHaveText('人员准备')
    await menu.getByRole('menuitem', { name: '系统准备', exact: true }).click()
    await expect(page).toHaveURL(/\/staffing\/system$/)
    await expect(page.getByRole('heading', { name: '系统准备', exact: true })).toBeVisible()
    await expect(menu.locator('.ant-menu-item-selected')).toHaveText('系统准备')
    await menu.getByRole('menuitem', { name: '项目列表', exact: true }).click()
    await expect(page).toHaveURL(/\/staffing$/)
    await expect(menu.locator('.ant-menu-item-selected')).toHaveText('项目列表')
    expect(await page.getByRole('link', { name: '人员准备', exact: true }).count()).toBe(0)
  })
}
