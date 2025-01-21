class Auth {
    static isAuthenticated() {
        return !!localStorage.getItem('token');
    }

    static async login(username, password) {
        try {
            const data = await api.login(username, password);
            localStorage.setItem('token', data.access_token);
            return true;
        } catch (error) {
            console.error('Login failed:', error);
            return false;
        }
    }

    static logout() {
        localStorage.removeItem('token');
        router.navigate('/');
    }

    static async register(username, name, password) {
        try {
            await api.register(username, name, password);
            return await this.login(username, password);
        } catch (error) {
            console.error('Registration failed:', error);
            return false;
        }
    }
}
