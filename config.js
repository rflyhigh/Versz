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
    ].join(' ')
};
