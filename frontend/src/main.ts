import { createApp } from 'vue';
import './style.css';
import App from './App.vue';
import { router } from './router';
createApp(App).use(router).mount('#app');
if (import.meta.env.PROD && 'serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register('/service-worker.js').catch(() => {});
  });
}
