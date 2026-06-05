import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'aiagent',
  description: 'AI agent desktop and web client documentation',
  base: '/kian_ai_0001/',
  ignoreDeadLinks: true,
  themeConfig: {
    nav: [
      { text: 'Home', link: '/' },
      { text: 'SLAM Web Viewer', link: '/SLAM_WEB_VIEWER_DESIGN' },
      { text: 'Web UI Plan', link: '/WEB_UI_ARCHITECTURE_PLAN' }
    ],
    sidebar: [
      {
        text: 'Guide',
        items: [
          { text: 'Overview', link: '/' },
          { text: 'SLAM Web Viewer', link: '/SLAM_WEB_VIEWER_DESIGN' },
          { text: 'Web UI Architecture Plan', link: '/WEB_UI_ARCHITECTURE_PLAN' },
          { text: 'ROS2 Native Humble Changelog', link: '/ROS2_NATIVE_HUMBLE_CHANGELOG' }
        ]
      },
      {
        text: 'Design',
        items: [
          { text: 'Voice Latency Optimization', link: '/VOICE_LATENCY_OPTIMIZATION' },
          { text: 'SLAM Base Map & No-Fly Zone', link: '/slam_base_map_and_nfz_design' },
          { text: 'SLAM Grasp Region', link: '/slam_grasp_region_design' }
        ]
      }
    ],
    socialLinks: [
      { icon: 'github', link: 'https://github.com/GGbond-dot/kian_ai_0001' }
    ]
  }
})
