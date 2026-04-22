/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        corp: {
          teal:       '#1A7080',
          'teal-lt':  '#4EC3C3',
          amber:      '#F5A800',
          red:        '#C8312A',
          navy:       '#1A3A4A',
          gray:       '#E8E8E8',
          'gray-md':  '#9CA3AF',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
