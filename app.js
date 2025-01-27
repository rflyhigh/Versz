class SpotifyTracker {
    constructor() {
        this.setupEventListeners();
        this.checkAuthCallback();
        this.checkExistingSession();
    }

    setupEventListeners() {
        document.getElementById('login-btn').addEventListener('click', () => this.login());
        document.getElementById('logout-btn').addEventListener('click', () => this.logout());
    }

    login() {
        const redirectUri = `${window.location.origin}/callback.html`;
        const authUrl = `https://accounts.spotify.com/authorize?client_id=${config.clientId}&response_type=code&redirect_uri=${encodeURIComponent(redirectUri)}&scope=${encodeURIComponent(config.scopes)}`;
        window.location.href = authUrl;
    }

    logout() {
        localStorage.removeItem('spotify_user_id');
        this.showLoginSection();
    }

    async checkAuthCallback() {
        const urlParams = new URLSearchParams(window.location.search);
        const code = urlParams.get('code');
        
        if (code) {
            try {
                const response = await fetch(`${config.backendUrl}/auth/callback?code=${code}`);
                const data = await response.json();
                
                if (data.success) {
                    localStorage.setItem('spotify_user_id', data.user_id);
                    window.location.href = '/';
                }
            } catch (error) {
                console.error('Authentication failed:', error);
            }
        }
    }

    checkExistingSession() {
        const userId = localStorage.getItem('spotify_user_id');
        if (userId) {
            this.showProfileSection(userId);
            this.startTracking(userId);
        } else {
            this.showLoginSection();
        }
    }

    showLoginSection() {
        document.getElementById('login-section').classList.remove('hidden');
        document.getElementById('profile-section').classList.add('hidden');
        document.getElementById('user-info').classList.add('hidden');
    }

    showProfileSection(userId) {
        document.getElementById('login-section').classList.add('hidden');
        document.getElementById('profile-section').classList.remove('hidden');
        document.getElementById('user-info').classList.remove('hidden');
        document.getElementById('username').textContent = `User: ${userId}`;
    }

    async startTracking(userId) {
        await this.updateCurrentTrack(userId);
        await this.updateRecentTracks(userId);
        setInterval(() => this.updateCurrentTrack(userId), 30000);
        setInterval(() => this.updateRecentTracks(userId), 120000);
    }

    async updateCurrentTrack(userId) {
        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}/currently-playing`);
            const data = await response.json();
            
            const currentTrackInfo = document.getElementById('current-track-info');
            if (data.is_playing) {
                currentTrackInfo.innerHTML = `
                    <div class="track-name">${data.track_name}</div>
                    <div class="track-artist">${data.artist_name}</div>
                `;
            } else {
                currentTrackInfo.innerHTML = '<p>Not playing anything right now</p>';
            }
        } catch (error) {
            console.error('Failed to update current track:', error);
        }
    }

    async updateRecentTracks(userId) {
        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}/recent-tracks`);
            const tracks = await response.json();
            
            const tracksList = document.getElementById('tracks-list');
            tracksList.innerHTML = tracks.map(track => `
                <div class="track-item">
                    <div class="track-name">${track.track_name}</div>
                    <div class="track-artist">${track.artist_name}</div>
                    <div class="track-time">${this.formatDate(track.played_at)}</div>
                </div>
            `).join('');
        } catch (error) {
            console.error('Failed to update recent tracks:', error);
        }
    }

    formatDate(dateString) {
        const date = new Date(dateString);
        return date.toLocaleString();
    }
}

new SpotifyTracker();
