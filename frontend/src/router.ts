import { createRouter, createWebHistory } from 'vue-router';
import { loadSession, session } from './api';
export const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    { path: '/login', component: () => import('./views/LoginView.vue') },
    { path: '/', component: () => import('./views/LibraryView.vue') },
    { path: '/videos/:id', component: () => import('./views/VideoView.vue') },
    { path: '/creators', component: () => import('./views/CreatorsView.vue') },
    { path: '/creators/:id', component: () => import('./views/CreatorView.vue') },
    { path: '/collections', component: () => import('./views/CollectionsView.vue') },
    {
      path: '/account/security',
      component: () => import('./views/SecurityView.vue'),
      meta: { login: true },
    },
    {
      path: '/admin/:section?',
      component: () => import('./views/AdminView.vue'),
      meta: { admin: true, login: true },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
});
router.beforeEach(async (to) => {
  if (!session.ready) {
    try {
      await loadSession();
    } catch {
      if (to.meta.login) return { path: '/login', query: { next: to.fullPath } };
    }
  }
  if (!session.user && to.meta.login) return { path: '/login', query: { next: to.fullPath } };
  if (session.user && to.path === '/login') return '/';
  if (to.meta.admin && !['admin', 'editor'].includes(session.user?.role || '')) return '/';
  if (
    to.meta.admin &&
    session.user?.role === 'editor' &&
    !['videos', 'creators'].includes(String(to.params.section))
  )
    return '/admin/videos';
});
