const API_URL = 'https://your-render-backend.onrender.com';

const api = {
    async register(username, name, password) {
        const response = await fetch(`${API_URL}/register`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ username, name, password })
        });
        return response.json();
    },

    async login(username, password) {
        const formData = new FormData();
        formData.append('username', username);
        formData.append('password', password);

        const response = await fetch(`${API_URL}/token`, {
            method: 'POST',
            body: formData
        });
        return response.json();
    },

    async getLyrics() {
        const response = await fetch(`${API_URL}/lyrics`, {
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            }
        });
        return response.json();
    },

    async createLyrics(content) {
        const response = await fetch(`${API_URL}/lyrics`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            },
            body: JSON.stringify(content)
        });
        return response.json();
    },

    async updateLyrics(id, content) {
        const response = await fetch(`${API_URL}/lyrics/${id}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            },
            body: JSON.stringify(content)
        });
        return response.json();
    },

    async shareLyrics(extension, content) {
        const response = await fetch(`${API_URL}/share`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            },
            body: JSON.stringify({ extension, content })
        });
        return response.json();
    },

    async getSharedLyrics(extension) {
        const response = await fetch(`${API_URL}/share/${extension}`);
        return response.json();
    }
};
