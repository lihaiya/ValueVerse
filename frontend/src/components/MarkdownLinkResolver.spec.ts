import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'
import MarkdownLinkResolver from './MarkdownLinkResolver.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: { template: '<div />' } },
    { path: '/wiki/:id', component: { template: '<div />' } },
  ],
})

describe('MarkdownLinkResolver', () => {
  it('renders known and missing wiki links', async () => {
    const wrapper = mount(MarkdownLinkResolver, {
      global: {
        plugins: [router],
        stubs: { 'el-tag': { template: '<span><slot /></span>' } },
      },
      props: {
        text: '查看 [[公司A]] 和 [[公司B]]',
        knownTitles: { 公司A: 'node-1' },
      },
    })
    expect(wrapper.text()).toContain('[[公司A]]')
    expect(wrapper.text()).toContain('待创建')
    expect(wrapper.find('a').attributes('href')).toBe('/wiki/node-1')
  })
})
