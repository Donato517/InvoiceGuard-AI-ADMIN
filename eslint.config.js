/**
 * @file ESLint 設定（Flat Config）
 * @description JS 建議規則 + Vue 建議規則，最後關閉與 Prettier 衝突的格式規則。
 */
import js from '@eslint/js'
import prettier from 'eslint-config-prettier/flat'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'

export default [
  { ignores: ['dist/**', 'coverage/**', 'tmp/**', 'public/**'] },
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    languageOptions: {
      globals: { ...globals.browser },
    },
  },
  prettier,
]
