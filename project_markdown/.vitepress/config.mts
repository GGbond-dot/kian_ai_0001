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
          { text: 'First Response Latency', link: '/FIRST_RESPONSE_LATENCY' },
          { text: 'SLAM Web Viewer', link: '/SLAM_WEB_VIEWER_DESIGN' },
          { text: 'Web UI Architecture Plan', link: '/WEB_UI_ARCHITECTURE_PLAN' },
          { text: 'ROS2 Native Humble Changelog', link: '/ROS2_NATIVE_HUMBLE_CHANGELOG' }
        ]
      },
      {
        text: 'Background',
        items: [
          { text: 'Background 01', link: '/background01' },
          { text: 'Background 02', link: '/background02' },
          { text: 'Background 03', link: '/background03' },
          { text: 'Background 04', link: '/background04' }
        ]
      }
    ],
    socialLinks: [
      { icon: 'github', link: 'https://github.com/GGbond-dot/kian_ai_0001' }
    ]
  }
})
