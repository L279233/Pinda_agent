import client from './client'

export interface NotificationItem {
  notification_id: string
  application_id?: string | null
  reminder_type: string
  title: string
  content: string
  deadline?: string | null
  sent_at: string
  read: boolean
}

export const notificationsApi = {
  list: (unreadOnly = false) =>
    client.get<{ items: NotificationItem[]; unread_count: number }>('/notifications', {
      params: { unread_only: unreadOnly },
    }),
  markRead: (notificationId: string) =>
    client.post<NotificationItem>(`/notifications/${notificationId}/read`),
}
