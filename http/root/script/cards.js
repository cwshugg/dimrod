// JS code used to create material-design cards for the material-design-lite
// theme I'm borrowing from Google.
//
//      Connor Shugg

// Card globals
const card_section_class = "section--center mdl-grid mdl-grid--no-spacing mdl-shadow--2dp";
const card_div_class = "mdl-card mdl-cell--12-col";
const card_color_bg = "mdl-color--grey-900";
const card_color_text = "mdl-color-text--grey-300";

// Main card class.
class Card
{
    // Constructor. Takes in an ID for the HTML element.
    constructor(id)
    {
        this.id = id;

        // create the 'section' wrapper element
        this.html = document.createElement("section");
        this.html.className = card_section_class;
        this.html.id = this.id + "_section";

        // create the inner div (the actual card)
        this.div = document.createElement("div");
        this.div.className = card_div_class + " " +
                             card_color_bg + " " +
                             card_color_text;
        this.div.id = this.id + "_div";
        this.html.appendChild(this.div);

        // create a supporting text div
        this.stdiv = document.createElement("div");
        this.stdiv.className = "mdl-card__supporting-text";
        this.stdiv.id = this.id + "_stdiv";
        this.div.appendChild(this.stdiv);

        // set up a few internal fields for other parts of the card
        this.title = null;
        this.text = null;
    }

    // Takes in a title and updates the card's title text.
    set_title(title)
    {
        // if the title doesn't exist, create it
        if (!this.title)
        {
            this.title = document.createElement("h2");
            this.title.id = this.id + "_title";

            // ensure the title is at the top
            if (this.stdiv.children.length > 0)
            { this.stdiv.insertBefore(this.title, this.stdiv.children[0]); }
            else
            { this.stdiv.appendChild(this.title); }
        }
        this.title.innerHTML = title;
    }
    
    // Takes in text and updates the card's supporting text.
    set_text(text)
    {
        // if the text paragraph doesn't exist, create it
        if (!this.text)
        {
            this.text = document.createElement("p");
            this.text.id = this.id + "_text";
            this.stdiv.appendChild(this.text);
        }
        this.text.innerHTML = text;
    }
}

