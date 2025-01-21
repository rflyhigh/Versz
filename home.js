function HomeView() {
    return `
        <div class="hero">
            <h1>Welcome to Versz</h1>
            <p>Create, style, and share your lyrics with the world.</p>
            <div class="auth-forms">
                <div class="auth-form">
                    <h2>Login</h2>
                    <form id="loginForm">
                        <input type="text" class="input" placeholder="Username" required>
                        <input type="password" class="input" placeholder="Password" required>
                        <button type="submit" class="btn">Login</button>
                    </form>
                </div>
                <div class="auth-form">
                    <h2>Register</h2>
                    <form id="registerForm">
                        <input type="text" class="input" placeholder="Username" required>
                        <input type="text" class="input" placeholder="Name" required>
                        <input type="password" class="input" placeholder="Password" required>
                        <button type="submit" class="btn">Register</button>
                    </form>
                </div>
            </div>
        </div>
    `;
}
