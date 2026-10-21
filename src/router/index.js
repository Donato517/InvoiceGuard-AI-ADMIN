/**
 * @file 路由設定
 * @description 定義後台所有頁面路由。頁面元件一律使用動態 import（lazy loading）。
 *   後台頁面預設都需要登入，只有 meta.public 為 true 的頁面可匿名存取。
 *
 * 路由 meta 欄位：
 *   - title {string}    頁面標題（顯示於瀏覽器分頁）
 *   - layout {string}   版型：admin（預設，側欄 + 頁首）/ blank（登入頁、錯誤頁）
 *   - public {boolean}  是否允許未登入存取
 *
 * 側欄選單定義於 constants/menu.js，新增頁面時一併更新。
 */
import { createRouter, createWebHistory } from 'vue-router'

import { setupRouterGuards } from '@/router/guards'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/login/LoginView.vue'),
    meta: { title: '登入', layout: 'blank', public: true },
  },
  {
    path: '/',
    redirect: { name: 'dashboard' },
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('@/views/dashboard/DashboardView.vue'),
    meta: { title: '儀表板' },
  },
  {
    path: '/invoices',
    name: 'invoice-review-list',
    component: () => import('@/views/invoice/InvoiceReviewListView.vue'),
    meta: { title: '發票審核' },
  },
  {
    path: '/invoices/:id',
    name: 'invoice-review-detail',
    component: () => import('@/views/invoice/InvoiceReviewDetailView.vue'),
    props: true,
    meta: { title: '發票審核詳情' },
  },
  {
    path: '/users',
    name: 'user-list',
    component: () => import('@/views/user/UserListView.vue'),
    meta: { title: '使用者管理' },
  },
  {
    path: '/system/job-runs',
    name: 'job-run-list',
    component: () => import('@/views/system/JobRunListView.vue'),
    meta: { title: '排程紀錄' },
  },
  {
    path: '/system/audit-logs',
    name: 'audit-log-list',
    component: () => import('@/views/system/AuditLogListView.vue'),
    meta: { title: '稽核紀錄' },
  },
  {
    path: '/403',
    name: 'forbidden',
    component: () => import('@/views/error/ForbiddenView.vue'),
    meta: { title: '沒有權限', layout: 'blank', public: true },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/error/NotFoundView.vue'),
    meta: { title: '找不到頁面', layout: 'blank', public: true },
  },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

setupRouterGuards(router)

export default router
