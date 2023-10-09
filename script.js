const backgroundImages = ["images/unsplash1.jpg", "images/unsplash2.jpg", "images/unsplash3.jpg"];
let currentImageIndex = 0;

const background = document.getElementById("background");
const text = document.getElementById("text");

function changeBackground() {
    text.style.opacity = 0;

    setTimeout(() => {
        background.style.backgroundImage = `url(${backgroundImages[currentImageIndex]})`;
        text.textContent = currentImageIndex === 0 ? "abc" : "sdd";
        text.style.opacity = 1;
        currentImageIndex = (currentImageIndex + 1) % backgroundImages.length;
    }, 1000);
}

background.style.backgroundImage = `url(${backgroundImages[currentImageIndex]})`;
text.textContent = "abc";

setInterval(changeBackground, 5000);
