import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', component: () => import('./views/MigrationHome.vue') },
  { path: '/new', component: () => import('./views/NewMigration.vue') },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
