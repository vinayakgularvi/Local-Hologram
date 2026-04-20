import { createRouter, createWebHistory } from "vue-router";
import WebRtcLive from "./WebRtcLive.vue";
import AnalyticsDashboard from "./AnalyticsDashboard.vue";
import AvatarDashboard from "./AvatarDashboard.vue";
export default createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: "/", name: "live", component: WebRtcLive },
    { path: "/analytics", name: "analytics", component: AnalyticsDashboard },
    { path: "/avatar", name: "avatar", component: AvatarDashboard },
    { path: "/analystics", redirect: "/analytics" },
  ],
});
