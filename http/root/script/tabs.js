// A class representing a single tab in the material design css/html template
// I'm borrowing from Google.
//
//      Connor Shugg

// Styling globals.
const tab_div_color_bg = "mdl-color--grey-800";

// Tab class.
class Tab
{
    // Constructor. Takes in an ID string a boolean indicating whether or not
    // the tab is active.
    constructor(id, is_active)
    {
        this.id = id;
        
        // create the 'div' that contains the tab's page content
        this.html = document.createElement("div");
        this.html.id = this.id;
        this.html.className = "mdl-layout__tab-panel " + tab_div_color_bg;
        if (is_active)
        { this.html.className += " is-active"; }

        // create the anchor that's used as the tab selector
        this.anchor = document.createElement("a");
        this.anchor.id = this.id + "_anchor";
        this.anchor.className = "mdl-layout__tab";
        if (is_active)
        { this.anchor.className += " is-active"; }
        this.anchor.href = "#" + this.html.id;
        this.anchor.innerHTML = this.id;
    }

    // Takes in a title and update's the tab's anchor text.
    set_title(title)
    {
        this.anchor.innerHTML = title;
    }
    
    // Takes in a Card object and adds its HTML to the internal div.
    add_card(card)
    {
        this.html.appendChild(card.html);
    }
}

