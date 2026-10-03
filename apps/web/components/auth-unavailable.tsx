export function AuthUnavailable() {
  return (
    <div className="notice error" role="alert">
      <h1>Сервис временно недоступен</h1>
      <p>Не удалось проверить сессию. Повторите попытку позже.</p>
    </div>
  );
}
