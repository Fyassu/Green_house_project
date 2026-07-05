const API = "http://localhost:5000";

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();

  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;
  const errorEl  = document.getElementById("error-msg");
  const btnText  = document.getElementById("btn-text");
  const spinner  = document.getElementById("btn-spinner");

  errorEl.textContent = "";
  btnText.style.display  = "none";
  spinner.style.display  = "inline";

  try {
    const res = await fetch(API + "/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();

    if (!res.ok) {
      errorEl.textContent = data.error || "Đăng nhập thất bại";
      return;
    }

    localStorage.setItem("gh_token",    data.token);
    localStorage.setItem("gh_username", data.username);
    window.location.href = "index.html";
  } catch {
    errorEl.textContent = "Không thể kết nối tới backend";
  } finally {
    btnText.style.display  = "inline";
    spinner.style.display  = "none";
  }
});
