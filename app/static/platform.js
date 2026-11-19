let current = /Win/i.test(navigator.userAgentData?.platform || navigator.platform || '')
  ? 'windows'
  : 'macos';
export function setPlatform(value) {
  if (value) current = value;
  document.documentElement.dataset.platform = current;
}
export const modifier = () => (current === 'macos' ? '⌘' : 'Ctrl+');
export const shortcut = (value) => value.replaceAll('⌘', modifier());
export const fileManager = () => (current === 'windows' ? '文件资源管理器' : '访达');
export const machine = () => (current === 'windows' ? '这台电脑' : '这台 Mac');
