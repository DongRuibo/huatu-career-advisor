// 浏览器端启动入口，把 Vue 根组件挂载到 HTML 节点，并引入工作台全局样式。

import { createApp } from 'vue'
import App from './App.vue'
import './style.css'

createApp(App).mount('#app')
