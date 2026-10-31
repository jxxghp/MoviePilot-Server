import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Versions from '../components/Versions.vue'

describe('版本分布', () => {
  it('切换 iOS 后展示安装数，并兼容旧服务的缺失字段', async () => {
    const wrapper = mount(Versions, { props: {
      backend: [{ version: 'v3.2.2', count: 200 }], frontend: [],
      ios: [{ version: '1.0.6', count: 32 }],
    } })
    await wrapper.get('[id$="trigger-ios"]').trigger('mousedown', { button: 0, ctrlKey: false })
    await wrapper.get('[id$="trigger-ios"]').trigger('click')
    expect(wrapper.text()).toContain('1.0.6')
    expect(wrapper.text()).toContain('32')
    expect(wrapper.text()).not.toContain('200')
    await wrapper.setProps({ ios: undefined })
    expect(wrapper.text()).toContain('暂无版本上报数据')
  })
})
