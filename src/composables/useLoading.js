/**
 * @file useLoading 組合式函式
 * @description 包裝非同步工作並自動管理 loading 狀態，避免每個頁面重複寫 try / finally。
 * @example
 * const { loading, run } = useLoading()
 * const page = await run(() => fetchInvoices({ page: 1 }))
 */
import { ref } from 'vue'

/**
 * @returns {{ loading: import('vue').Ref<boolean>, run: <T>(task: () => Promise<T>) => Promise<T> }}
 */
export function useLoading() {
  const loading = ref(false)

  /**
   * 執行非同步工作，執行期間 loading 為 true
   * @template T
   * @param {() => Promise<T>} task - 要執行的非同步函式
   * @returns {Promise<T>} task 的結果
   */
  async function run(task) {
    loading.value = true
    try {
      return await task()
    } finally {
      loading.value = false
    }
  }

  return { loading, run }
}
