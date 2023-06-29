"use client"
import styles from '@styles/main.css'
import {Row,Container,Col,Stack  } from "react-bootstrap"
import ConfigSideNav from '@components/ConfigSideNav'
import MainContainer from '@components/MainContainer'
import { ToastContainer, toast } from 'react-toastify';
import  { useState } from "react";


export default function Home() {
    const [username, setUsername] = useState("None");

  return (
  <>
     <Row className=' pe-3 vh-100 overflow-hidden  g-0'>
        <Col className="side-bar-col" lg={3} xs={3}>
        <div>
        <div className='d-flex align-items-center justify-content-center py-4'><h3>Alpine AI</h3></div>
       <ConfigSideNav  onUser={setUsername}/>
        </div>
        
        </Col>
        <Col lg={9} xs={9}
          className="main-chat-col mt-3"
        >
<MainContainer username={username}/>
        </Col>
        <ToastContainer />
      </Row>
   
  </>
  )
}
