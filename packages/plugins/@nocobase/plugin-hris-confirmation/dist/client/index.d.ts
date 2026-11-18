import { Plugin } from '@nocobase/client';

/**
 * HRIS 转正审批客户端插件（空生命周期实现）。
 * 仅有受控动作的服务端逻辑；前端按钮接线见 scripts/build_ui.py。
 */
export default class PluginHrisConfirmationClient extends Plugin {}
