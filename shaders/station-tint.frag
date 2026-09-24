#version 440
// Duotone for station artwork. Each pixel's luminance is mapped between two
// theme colours: black -> ink (foreground or accent), white -> paper (a panel
// surface tone). On dark themes that folds a logo's white background into the
// panel and draws the mark itself in the theme's ink; on light themes it reads
// like a print. Based on Hertz's station-tint shader.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec4 ink;
    vec4 paper;
    float strength;
};
layout(binding = 1) uniform sampler2D source;

void main() {
    vec4 pixel = texture(source, qt_TexCoord0);
    // Qt textures are premultiplied: map the original RGB, then restore alpha.
    vec3 rgb = pixel.a > 0.0 ? pixel.rgb / pixel.a : vec3(0.0);
    float luma = clamp(dot(rgb, vec3(0.2126, 0.7152, 0.0722)), 0.0, 1.0);
    // Slight contrast lift so washed-out logos still separate from the paper.
    luma = smoothstep(0.04, 0.96, luma);
    vec3 duo = mix(ink.rgb, paper.rgb, luma);
    fragColor = vec4(mix(rgb, duo, strength) * pixel.a, pixel.a) * qt_Opacity;
}
