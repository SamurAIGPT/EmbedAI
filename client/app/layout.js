import '@styles/globals.css'
import { Inter } from 'next/font/google'
import 'bootstrap/dist/css/bootstrap.css';
import "react-toastify/dist/ReactToastify.css";



const inter = Inter({ subsets: ['latin'] })

export const metadata = {
  title: 'Alpine AI',
  description: 'Alpine AI Enterprise Document Search',
}

export default function RootLayout({ children }) {
  return (
    <html lang="en">
    
      <body className={inter.className}>{children}</body>
      
    </html>
  )
}
