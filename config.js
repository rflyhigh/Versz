const config = {
    backendUrl: 'https://versz.onrender.com',
    clientId: 'e4625fedf8a24040aa6030051efcd883',
    redirectUri: 'https://versz.fun/callback.html',
    scopes: [
        'user-read-currently-playing',
        'user-read-recently-played',
        'user-read-playback-state',
        'user-top-read',
        'playlist-read-collaborative',
        'playlist-read-private',
        'playlist-modify-public',
        'playlist-modify-private',
        'user-read-private',
        'user-read-email'
    ].join(' ')
};

