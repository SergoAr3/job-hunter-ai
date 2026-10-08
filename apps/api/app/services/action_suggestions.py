"""User-invoked presets, never automatic scheduling."""
_PRESETS = {
    "saved": [("prepare_application", "Подготовить отклик")],
    "applied": [("check_reply_3d", "Проверить ответ через 3 дня"), ("follow_up_5d", "Написать рекрутеру через 5 дней")],
    "recruiter_response": [("reply_recruiter", "Ответить рекрутеру"), ("clarify_steps", "Уточнить следующие шаги")],
    "interview": [("prepare_interview", "Подготовиться к интервью"), ("post_interview", "Отправить follow-up после интервью")],
    "offer": [("reply_offer", "Ответить по офферу"), ("clarify_offer", "Уточнить условия")],
}


def suggestions_for(status):
    return [{"id": key, "label": label, "action_text": label} for key, label in _PRESETS.get(status, [])]
