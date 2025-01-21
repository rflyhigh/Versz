function EditorView() {
    return `
        <div class="container">
            <div class="toolbar">
                <select id="fontSize">
                    <option value="14">14px</option>
                    <option value="16">16px</option>
                    <option value="18" selected>18px</option>
                    <option value="20">20px</option>
                    <option value="24">24px</option>
                </select>
                <select id="textColor">
                    <option value="inherit">Default</option>
                    <option value="#FF6B6B">Red</option>
                    <option value="#4ECDC4">Cyan</option>
                    <option value="#FFE66D">Yellow</option>
                    <option value="#95E1D3">Mint</option>
                </select>
                <select id="textFormat">
                    <option value="none">Normal</option>
                    <option value="uppercase">Uppercase</option>
                    <option value="lowercase">Lowercase</option>
                    <option value="capitalize">Capitalize</option>
                </select>
                <button onclick="toggleTheme()">Theme</button>
                <button id="shareButton">Share</button>
            </div>
            
            <div class="header">
                <input type="text" class="title" placeholder="Title">
                <input type="text" class="subtitle" placeholder="Artist">
            </div>
            
            <div class="lyrics-container" 
                 contenteditable="true" 
                 placeholder="Enter your lyrics here..."
                 id="lyrics"></div>

            <button class="clean-mode-toggle" onclick="toggleCleanMode()">
                Toggle Clean Mode
            </button>
        </div>
    `;
}
