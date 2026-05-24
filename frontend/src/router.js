import { createRouter, createWebHistory } from "vue-router";
import WebRtcLive from "./WebRtcLive.vue";
import AnalyticsDashboard from "./AnalyticsDashboard.vue";
import AvatarDashboard from "./AvatarDashboard.vue";
import VideoRagDashboard from "./VideoRagDashboard.vue";
export default createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: "/hologram", name: "live", component: WebRtcLive },
    { path: "/", redirect: "/hologram" },
    { path: "/analytics", name: "analytics", component: AnalyticsDashboard },
    { path: "/avatar", name: "avatar", component: AvatarDashboard },
    { path: "/video-rag", name: "videoRag", component: VideoRagDashboard },
    { path: "/analystics", redirect: "/analytics" },
  ],
});
