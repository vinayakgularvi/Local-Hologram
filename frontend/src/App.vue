<script setup>
import WebRtcLive from "./WebRtcLive.vue";
</script>

<template>
  <div class="app-root">
    <WebRtcLive />
  </div>
</template>

<style>
:root {
  --bg: #ffffff;
  --text: #1a1a1a;
  --muted: #5c5c5c;
  --accent: #00c8c8;
  --accent2: #7b61ff;
}

*,
*::before,
*::after {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-height: 100dvh;
  font-family: "DM Sans", system-ui, sans-serif;
  background: #fff;
  color: var(--text);
}

#app {
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
}

.app-root {
  flex: 1;
  min-height: 0;
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: flex-start;
  padding: 0;
}
</style>
